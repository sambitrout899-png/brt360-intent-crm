"""BIM - BRT Integration Manager (models/bim.py): loading data into BRT 360
in batches (CSV / JSON) or record by record over the API (EAI).

The rules every load follows:
- Every record carries a BRT_ID that BRT 360 issued for that entity
  (brt_id.issued: right prefix, BRT 360's check character, and a number
  the register actually handed out). Loaders get them in advance from
  reserve_ids - or the interface names the source's own id (key_field):
  BRT 360 issues the BRT_ID the first time it sees that id and uses the same
  one ever after, so a source system never has to store our ids. A reference
  can use the source's ids too (party_external_id).
- Mappings can transform, not only rename (hub/mapping.py).
- The rows a load refused are kept, encrypted, to fix and reprocess.
- A known BRT_ID updates that record (interface mode "upsert") or is
  refused ("insert"); a BRT_ID of another tenant's record is refused.
- References are by BRT_ID too (a lead's party_brt_id), and may point at
  records loaded earlier in the same batch.
- Each row is loaded on its own (a savepoint): a bad row is reported and
  skipped, never half-written; a dry run checks everything and keeps
  nothing.
- Prospects and leads also pass their Page Designer page (mandatory
  fields, field types, validation rules) unless the load says otherwise.
- A load of more than SYNC_ROWS rows runs in the background: the request
  answers with the queued job at once and its progress is read from the
  job (load_in_background) - a 10,000-row load no longer has to fit in one
  request's time.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from app import brt_id
from app.crm_core import custom_field_service, page_service, process_service
from app.db import cached, forget
from app.hub import mapping
from app.models.auth import User
from app.models.bim import BimExternalRef, BimInterface, BimJob
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
    if fmt == "xlsx":  # the Excel template, filled in - sent base64-encoded; its first sheet is the data
        import base64

        from app.documents import xlsx_to_csv

        try:
            content = xlsx_to_csv(base64.b64decode(content))
        except Exception as exc:  # noqa: BLE001 - not base64, not a zip, not a workbook: all the sender's to fix
            raise ValueError("That Excel file can't be read - send it base64-encoded, or save it as CSV") from exc
        fmt = "csv"
    if fmt == "json":
        data = json.loads(content)
        rows = data.get("records") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("JSON must be a list of records (or {\"records\": [...]})")
        return rows
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    return [{(k or "").strip(): v for k, v in row.items() if k} for row in reader]


def records_in(answer: Any, path: str = "") -> list[dict[str, Any]]:
    """The records in another system's answer: at `path`, else the list it obviously holds."""
    found = mapping.dig(answer, path) if path else answer
    if isinstance(found, list):
        return [r for r in found if isinstance(r, dict)]
    if not isinstance(found, dict):
        return []
    if not path:
        for key in ("records", "items", "data", "results", "rows", "record", "item"):
            inner = found.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
            if isinstance(inner, dict) and key in ("record", "item"):
                return [inner]
        lists = [v for v in found.values() if isinstance(v, list) and v and all(isinstance(r, dict) for r in v)]
        if lists:
            return lists[0]
    return [found]


# ---------------------------------------------------------------- the source system's own ids


def _ref_of(db: Session, tenant_id: int, system: str, entity: str, external_id: str) -> str | None:
    row = db.query(BimExternalRef.record_brt_id).filter(BimExternalRef.tenant_id == tenant_id, BimExternalRef.system == system,
                                                        BimExternalRef.entity == entity, BimExternalRef.external_id == external_id).first()
    return row[0] if row else None


def external_ref(db: Session, tenant_id: int, system: str, entity: str, external_id: str) -> str | None:
    return _ref_of(db, tenant_id, str(system).strip().upper(), entity, str(external_id).strip())


def _entity_of(model: Any) -> str | None:
    return next((key for key, spec in ENTITIES.items() if spec.model is model), None)


def _apply_external_keys(db: Session, tenant_id: int, system: str, key_field: str, entity: str, spec: Spec, raw: dict[str, Any], row: dict[str, Any]) -> tuple[str, str] | None:
    """Fills the row's BRT_ID from the source's own id (issuing one the first time) and its references from the source's
    ids. Runs outside the row's savepoint - a number once given is never given again, even if the row is then refused.
    Returns (external id, BRT_ID) when this id has no link yet: the caller saves it with the record."""
    for ref, (model, _attr) in spec.refs.items():
        column = ref.removesuffix("_brt_id") + "_external_id"
        wanted = _str(row.pop(column, None))
        if wanted is not None and _str(row.get(ref)) is None:
            target = _ref_of(db, tenant_id, system, _entity_of(model) or "", wanted)
            if target is None:
                raise ValueError(f"{column} {wanted}: {system} has not loaded that record yet")
            row[ref] = target
    if not key_field:
        return None
    external = _str(mapping.dig(raw, key_field))
    row.pop(key_field.strip().lower().replace(" ", "_"), None)
    if external is None:
        raise ValueError(f"{key_field} is missing - it is this system's id for the record")
    if len(external) > 200:
        raise ValueError(f"{key_field} is longer than 200 characters")
    known = _ref_of(db, tenant_id, system, entity, external)
    given = _str(row.get("brt_id"))
    if known is not None:
        if given is not None and given.upper() != known:
            raise ValueError(f"{key_field} {external} is already {known}, not {given.upper()}")
        row["brt_id"] = known
        return None
    code = given.upper() if given else _issue(db, spec.table)
    row["brt_id"] = code
    return external, code


def _issue(db: Session, table: str) -> str:
    code = brt_id.allocate(db.connection(), table)
    forget(db, ("brt_id_top",))  # the top of the register moved: this code must count as issued
    return code


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
    # the register's top, read once per load: the codes were reserved before it started
    top = cached(db, ("brt_id_top",), (), lambda: brt_id.counter_top(db.connection()))
    if not brt_id.issued(db.connection(), code, spec.table, top=top):
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
        _record_owner(db, tenant_id, spec.entity_type, obj.id, owner_id, new=status == "inserted")
    db.flush()
    return status, []


def _record_owner(db: Session, tenant_id: int, entity_type: str, entity_id: int, user_id: int | None, new: bool = False) -> None:
    """The record's owner for visibility (same data intents keep). A record
    this load just inserted has none yet: written whole in one go."""
    from app.crm_core import lifecycle_service
    from app.models.lifecycle import EntityRecordMeta

    if user_id is None or entity_type not in ("party", "lead", "opportunity", "quote", "order"):
        return
    if new:
        db.add(EntityRecordMeta(tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id,
                                created_by_user_id=user_id, owner_user_id=user_id))
        return
    meta = lifecycle_service.get_meta(db, tenant_id, entity_type, entity_id, create=True)
    if meta.created_by_user_id is None:
        meta.created_by_user_id = user_id
    if meta.owner_user_id is None:
        meta.owner_user_id = user_id


def new_job(db: Session, tenant_id: int, entity: str, rows: list[dict[str, Any]], channel: str = "batch",
            interface: BimInterface | None = None, file_name: str | None = None, dry_run: bool = False) -> BimJob:
    spec_of(entity)  # an unknown entity is refused before anything is recorded
    if len(rows) > MAX_ROWS:
        raise ValueError(f"At most {MAX_ROWS} records per load - split the file")
    job = BimJob(tenant_id=tenant_id, interface_id=interface.id if interface else None, entity=entity, channel=channel,
                 file_name=file_name, dry_run=dry_run, total=len(rows))
    db.add(job)
    db.flush()
    return job


def run(db: Session, tenant_id: int, entity: str, rows: list[dict[str, Any]], channel: str = "batch", interface: BimInterface | None = None,
        file_name: str | None = None, dry_run: bool = False, apply_pages: bool = True, actor_id: int | None = None,
        job: BimJob | None = None) -> tuple[BimJob, list[dict[str, Any]]]:
    """Loads the rows (into `job`, or a new one). Each row on its own: a bad row is reported and skipped."""
    spec = spec_of(entity)
    # read once: the session lets go of everything it holds as a large load goes on (below)
    rules = mapping.load_mapping(interface.mapping_json) if interface else {}
    mode = interface.mode if interface else "upsert"
    keep_unmapped = not (interface and interface.ignore_unmapped)
    key_field = (interface.key_field or "").strip() if interface else ""
    system = ((interface.source_system or interface.code) if interface else "").strip().upper()
    ack_operation_id = interface.ack_operation_id if interface else None
    interface_code = interface.code if interface else None
    if job is None:
        job = new_job(db, tenant_id, entity, rows, channel, interface, file_name, dry_run)
    job.status = "running"
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    refused_rows: list[dict[str, Any]] = []
    counts = {"inserted": 0, "updated": 0, "failed": 0}
    outer = db.begin_nested() if dry_run else None
    for n, raw in enumerate(rows, start=1):
        row: dict[str, Any] = {}
        new_ref = None
        problems: list[str] = []
        status = ""
        try:
            row = mapping.apply(raw, rules, keep_unmapped)
            new_ref = _apply_external_keys(db, tenant_id, system, key_field, entity, spec, raw, row)
        except ValueError as exc:
            status, problems = "failed", [str(exc)]
        savepoint = db.begin_nested()
        if status != "failed":
            try:
                status, problems = load_row(db, tenant_id, spec, row, mode, apply_pages, actor_id)
                if status != "failed" and new_ref is not None:
                    db.add(BimExternalRef(tenant_id=tenant_id, system=system, entity=entity, external_id=new_ref[0], record_brt_id=new_ref[1]))
                    db.flush()  # the rows after this one may refer to it
            except Exception as exc:  # noqa: BLE001 - one bad row never stops the load
                status, problems = "failed", [f"could not be loaded: {exc}"]
        if status == "failed":
            savepoint.rollback()
            errors.append({"row": n, "brt_id": _str(row.get("brt_id")), "errors": problems})
            if len(refused_rows) < 1000:
                refused_rows.append(raw)
        else:
            savepoint.commit()
        counts[status] += 1
        results.append({"row": n, "brt_id": _str(row.get("brt_id")), "status": status, "errors": problems,
                        **({"external_id": _str(mapping.dig(raw, key_field))} if key_field else {})})
        if n % RELEASE_EVERY == 0:
            # The session keeps every row it saved, and each flush looks through all of them -
            # a 10,000-row load slowed down row by row. Saved rows are let go (still in the
            # transaction, read back from the database when needed); so are the cached lookups
            # that hold them.
            job_id = job.id
            db.flush()
            db.expunge_all()
            db.info.pop("brt_cache", None)
            db.info.pop("brt_cache_index", None)
            job = db.get(BimJob, job_id)
    if outer is not None:
        outer.rollback()  # a dry run keeps only the job's report
    job.inserted, job.updated, job.failed = counts["inserted"], counts["updated"], counts["failed"]
    job.errors_json = json.dumps(errors[:1000])
    job.failed_rows_json = json.dumps(refused_rows, default=str) if refused_rows and not dry_run else ""
    job.status = "completed" if not errors else ("failed" if len(errors) == len(rows) else "completed_with_errors")
    job.finished_at = utcnow()
    db.flush()
    if ack_operation_id and not dry_run:
        _acknowledge(db, tenant_id, ack_operation_id, job, interface_code)
    return job, results


def _acknowledge(db: Session, tenant_id: int, operation_id: int, job: BimJob, interface_code: str | None) -> None:
    """Tells the source how its load went - queued with the load, so it goes out only if the load is kept."""
    from app.hub import outbound
    from app.models.integration_hub import HubConnection, HubOperation

    op = db.get(HubOperation, operation_id)
    conn = db.get(HubConnection, op.connection_id) if op else None
    if op is None or conn is None or not op.is_active or not conn.is_active:
        return
    msg = outbound.enqueue(db, tenant_id, op, conn, {"interface": interface_code, "job": job.brt_id, "status": job.status, "total": job.total,
                                                     "inserted": job.inserted, "updated": job.updated, "failed": job.failed,
                                                     "errors": json.loads(job.errors_json or "[]")[:100]},
                           kind="ack", source=f"bim:{interface_code or job.id}"[:100])
    msg.mode = "async"


def reprocess(db: Session, tenant_id: int, job: BimJob, rows: list[dict[str, Any]] | None = None, dry_run: bool = False,
              actor_id: int | None = None) -> tuple[BimJob, list[dict[str, Any]]]:
    """Loads a job's refused rows again - as they were (the interface or the target data has been fixed), or as corrected."""
    if rows is None:
        rows = failed_rows(job)
    if not rows:
        raise ValueError("This load has no refused rows to reprocess")
    interface = db.get(BimInterface, job.interface_id) if job.interface_id else None
    fresh, results = run(db, tenant_id, job.entity, rows, job.channel, interface, job.file_name, dry_run, True, actor_id)
    fresh.parent_job_id = job.id
    if not dry_run:
        job.failed_rows_json = ""  # they live on the new job now, if still refused
    db.flush()
    return fresh, results


def failed_rows(job: BimJob) -> list[dict[str, Any]]:
    try:
        rows = json.loads(job.failed_rows_json or "[]")
    except ValueError:
        return []
    return rows if isinstance(rows, list) else []


def errors_csv(job: BimJob) -> str:
    """The refused rows with why, as a file to fix and load again."""
    rows = failed_rows(job)
    reasons = {e.get("row"): e for e in json.loads(job.errors_json or "[]")}
    columns: list[str] = []
    for row in rows:
        columns += [k for k in row if k not in columns]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["_row", "_errors", *columns])
    for (number, entry), row in zip(sorted(reasons.items()), rows):
        cells = [row.get(c) for c in columns]
        writer.writerow([number, "; ".join(entry.get("errors") or []), *[_csv_safe(json.dumps(c, default=str) if isinstance(c, (dict, list)) else c) for c in cells]])
    return out.getvalue()


def _csv_safe(value: Any) -> Any:
    """A value starting with = + - @ would run as a formula when the file is opened in Excel."""
    return f"'{value}" if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def changes(db: Session, tenant_id: int, interface: BimInterface, since: str = "", limit: int = 100) -> dict[str, Any]:
    """The change feed: records of the interface's entity created or changed since the cursor, oldest first."""
    from sqlalchemy import and_, func, or_

    spec = spec_of(interface.entity)
    model = spec.model
    stamp = func.coalesce(model.sys_updated_at, model.sys_created_at)
    query = db.query(model).filter(model.tenant_id == tenant_id, stamp.isnot(None))
    if since:
        when, _, last_id = since.partition("|")
        try:
            moment = dt.datetime.fromisoformat(when.replace("Z", "+00:00")).replace(tzinfo=None)
            after = int(last_id or 0)
        except ValueError as exc:
            raise ValueError("since is the next_cursor of an earlier answer, or a date and time") from exc
        query = query.filter(or_(stamp > moment, and_(stamp == moment, model.id > after)))
    rows = query.order_by(stamp, model.id).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    system = (interface.source_system or interface.code).strip().upper()
    refs = {r.record_brt_id: r.external_id for r in db.query(BimExternalRef).filter(
        BimExternalRef.tenant_id == tenant_id, BimExternalRef.system == system, BimExternalRef.entity == interface.entity,
        BimExternalRef.record_brt_id.in_([r.brt_id for r in rows] or [""]))}
    records = []
    for row in rows:
        record = record_by_brt_id(db, tenant_id, row.brt_id) or {"brt_id": row.brt_id}
        if row.brt_id in refs:
            record["external_id"] = refs[row.brt_id]
        records.append(record)
    cursor = since
    if rows:
        last = rows[-1]
        cursor = f"{(last.sys_updated_at or last.sys_created_at).isoformat()}|{last.id}"
    return {"entity": interface.entity, "records": records, "next_cursor": cursor, "has_more": more}


SYNC_ROWS = 1000  # loads up to this size answer at once; larger ones run in the background (load_in_background)
RELEASE_EVERY = 200  # rows after which a load lets go of the records it saved (run)
STALE = dt.timedelta(hours=1)


def load_in_background(job_id: int, tenant_id: int, entity: str, rows: list[dict[str, Any]], interface_id: int | None,
                       apply_pages: bool, actor_id: int | None) -> None:
    """Runs a queued load in its own session, after the request that queued it has answered."""
    from app import rls
    from app.db import SessionLocal

    marker = brt_id.current_user.set({"user_id": actor_id} if actor_id else None)
    try:
        with SessionLocal() as db:
            rls.scope(db, tenant_id)
            job = db.get(BimJob, job_id)
            interface = db.get(BimInterface, interface_id) if interface_id else None
            try:
                run(db, tenant_id, entity, rows, job.channel, interface, job.file_name, False, apply_pages, actor_id, job=job)
                db.commit()
            except Exception as exc:  # noqa: BLE001 - the job records why, rather than hanging as "running"
                db.rollback()
                rls.scope(db, tenant_id)
                job = db.get(BimJob, job_id)
                job.status, job.finished_at = "failed", utcnow()
                job.errors_json = json.dumps([{"row": 0, "brt_id": None, "errors": [f"the load stopped: {exc}"]}])
                db.commit()
    finally:
        brt_id.current_user.reset(marker)


def job_out(job: BimJob) -> dict[str, Any]:
    if job.status in ("queued", "running") and job.started_at and utcnow() - job.started_at > STALE:
        job.status = "interrupted"  # its process ended before it did (a restart) - shown, not saved
    return _job_out(job)


def _job_out(job: BimJob) -> dict[str, Any]:
    return {
        "id": job.id, "brt_id": job.brt_id, "entity": job.entity, "channel": job.channel, "file_name": job.file_name,
        "status": job.status, "dry_run": job.dry_run, "total": job.total, "inserted": job.inserted, "updated": job.updated,
        "failed": job.failed, "errors": json.loads(job.errors_json or "[]"), "started_at": job.started_at, "finished_at": job.finished_at,
        "interface_id": job.interface_id, "started_by": job.sys_created_by, "can_reprocess": bool(job.failed_rows_json), "parent_job_id": job.parent_job_id,
    }


def interface_out(row: BimInterface) -> dict[str, Any]:
    return {"id": row.id, "brt_id": row.brt_id, "code": row.code, "name": row.name, "entity": row.entity, "mode": row.mode,
            "mapping": json.loads(row.mapping_json or "{}"), "description": row.description, "is_active": row.is_active,
            "ignore_unmapped": bool(row.ignore_unmapped), "source_system": row.source_system or "", "key_field": row.key_field or "",
            "ack_operation_id": row.ack_operation_id, "pull_operation_id": row.pull_operation_id, "pull_every_minutes": row.pull_every_minutes or 0,
            "pull_records_path": row.pull_records_path or "", "pull_last_at": row.pull_last_at, "pull_cursor": row.pull_cursor or ""}


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

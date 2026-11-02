"""Bulk orders - many orders in one request, each run through the same single-transaction process an
agent runs by hand and followed to the end: fulfilment complete, services installed as assets, billing
started (models/bulk_orders.py).

Every step of a row is an intent run through the intent pipeline (routes_intent._handle) as the person
who submitted the request - with their position and data scope - so it gets the same checks, catalog
availability, approval matrix, Flow Studio before / after flows, fulfilment triggers and audit trail
as a single order. A row that can't go on stops with the reason; the others go ahead.

  b2c_new_connect  INT-41 Add a consumer (when the row names a new customer) -> INT-104 Place a B2C
                   order (feasibility, payment, submit; a discount over the limit waits for approval)
                   -> INT-25 Approve the order (Order Control; starts the fulfilment pattern) ->
                   INT-117 Send to Fulfillment (when no pattern started) -> COM / SOM / TOM ->
                   assets, billing start, order complete
  b2b_quote_order  INT-24 Create the order from an approved, accepted quote -> INT-25 -> INT-117 -> ...
  mobile_lines     INT-41 / INT-15 (new customer) -> INT-136 Activate mobile lines (one order line per
                   line, fulfilled by the mobile pattern or at once) -> lines on their assets
  asset_change     INT-107 Change an asset (a change order) -> INT-117 (a pattern for change orders)
                   or INT-108 Confirm order fulfillment -> the asset updated

The scheduler moves running requests along every minute (rows waiting for an engineer, an approver or
an inventory answer simply wait); "process now" does it at once.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.bulk_orders import FINAL_STAGES, PROCESSES, BulkOrder, BulkOrderRow
from app.timeutils import utcnow

MAX_ROWS = 5000
ROWS_PER_TICK = 25  # rows placed per tick and request; in-flight rows are all re-checked

# what each process reads from a row - (column, required, help)
PROCESS_INFO: dict[str, dict[str, Any]] = {
    "b2c_new_connect": {
        "label": "B2C new connect", "intents": ["INT-41", "INT-104", "INT-25", "INT-117"],
        "columns": [("party_id", False, "An existing B2C customer - or give customer_name to add one"),
                    ("customer_name", False, "A new consumer's name"), ("email", False, ""), ("phone", False, ""),
                    ("products", True, "e.g. Home Fiber 100; Wi-Fi Router x 1"), ("installation_address", False, "Where the service goes"),
                    ("order_channel", False, "store, web, app, call_centre ..."), ("payment_method", False, "card / cash ... (blank = billed)"),
                    ("channel_reference", False, "")],
    },
    "b2b_quote_order": {
        "label": "B2B quote to order", "intents": ["INT-24", "INT-25", "INT-117"],
        "columns": [("quote_id", True, "An approved quote the customer accepted (proposal or matched PO)")],
    },
    "mobile_lines": {
        "label": "Mobile lines", "intents": ["INT-41", "INT-15", "INT-136"],
        "columns": [("party_id", False, "An existing customer - or customer_name (+ segment) to add one"),
                    ("customer_name", False, ""), ("segment", False, "B2C (default) or B2B"), ("email", False, ""),
                    ("offering_name", True, "A mobile plan (catalog product on the mobile line)"), ("quantity", False, "Lines on this row (default 1)"),
                    ("pricing_attributes", False, "e.g. Data allowance=Unlimited"), ("sim_type", False, "physical / esim"),
                    ("msisdn", False, "Chosen numbers, comma separated"), ("iccid", False, "SIM ICCIDs, comma separated"),
                    ("user_name", False, "Users, comma separated"), ("initial_recharge", False, "Prepaid"), ("payment_method", False, ""),
                    ("deposit", False, "Postpaid"), ("credit_limit", False, "Postpaid"), ("wholesale_partner", False, "MVNO / reseller brand")],
    },
    "asset_change": {
        "label": "Asset changes", "intents": ["INT-107", "INT-117", "INT-108"],
        "columns": [("asset_id", True, "The installed service"), ("asset_operation", True, "modify, product_change, suspend, resume, terminate, "
                                                                                          "relocate, number_portability, port_out ..."),
                    ("offering_name", False, "Product change: the new product"), ("pricing_attributes", False, "e.g. Bandwidth=1 Gbps"),
                    ("technical_attributes", False, ""), ("quantity", False, ""), ("port_number", False, ""), ("donor_operator", False, ""),
                    ("recipient_operator", False, ""), ("target_site", False, ""), ("target_service_account_id", False, ""),
                    ("promotion", False, ""), ("due_date", False, "YYYY-MM-DD"), ("waive_penalty", False, "yes / no")],
    },
}
_DICT_COLUMNS = ("pricing_attributes", "technical_attributes")
_INT_COLUMNS = ("party_id", "quote_id", "asset_id", "quantity", "target_service_account_id")


def processes() -> list[dict[str, Any]]:
    return [{"process": k, "label": v["label"], "intents": v["intents"],
             "columns": [{"name": c, "required": r, "help": h} for c, r, h in v["columns"]],
             "csv_header": ",".join(c for c, _, _ in v["columns"])} for k, v in PROCESS_INFO.items()]


# ------------------------------------------------------------------ rows in


def _key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


def _pairs(value: Any) -> dict[str, Any]:
    """'Data allowance=Unlimited; SIM type=eSIM' -> {...}; a dict stays a dict."""
    if isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for part in re.split(r"[;|\n]", str(value or "")):
        if "=" in part or ":" in part:
            k, v = re.split(r"[=:]", part, maxsplit=1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def clean_row(process: str, raw: dict[str, Any]) -> dict[str, Any]:
    known = {c for c, _, _ in PROCESS_INFO[process]["columns"]}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = _key(k)
        if key not in known or v in (None, ""):
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        if key in _DICT_COLUMNS:
            v = _pairs(v)
        elif key in _INT_COLUMNS:
            try:
                v = int(float(v))
            except (TypeError, ValueError):
                pass  # reported by validation
        out[key] = v
    return out


MAX_FILE_BYTES = 5 * 1024 * 1024
# what people call a column -> the column (a header is matched ignoring case, spaces and punctuation)
ALIASES = {"customer": "customer_name", "name": "customer_name", "customer_id": "party_id", "account_id": "party_id", "party": "party_id",
           "product": "products", "offering": "offering_name", "plan": "offering_name", "address": "installation_address",
           "site_address": "installation_address", "channel": "order_channel", "operation": "asset_operation", "asset": "asset_id",
           "quote": "quote_id", "quote_number": "quote_id", "mobile": "msisdn", "number": "msisdn", "sim": "iccid", "lines": "quantity",
           "qty": "quantity", "user": "user_name", "users": "user_name", "partner": "wholesale_partner", "mail": "email", "e_mail": "email",
           "mobile_number": "phone", "phone_number": "phone", "recharge": "initial_recharge", "options": "pricing_attributes"}


def decode_file(raw: bytes) -> str:
    """An uploaded CSV file's text: UTF-8 (with or without the byte-order mark Excel adds), else Windows-1252."""
    if not raw or not raw.strip():
        raise ValueError("The file is empty")
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"The file is {len(raw) / 1024 / 1024:.1f} MB - at most {MAX_FILE_BYTES // 1024 // 1024} MB")
    if b"\x00" in raw[:4096]:
        raise ValueError("That isn't a CSV file (it looks like a spreadsheet or binary file) - save it as CSV first")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("The file's characters can't be read - save it as CSV UTF-8")


def _column(process: str, header: str) -> str:
    key = _key(header)
    return ALIASES.get(key, key) if ALIASES.get(key, key) in {c for c, _, _ in PROCESS_INFO[process]["columns"]} else key


def parse_csv(text: str) -> list[dict[str, Any]]:
    """Rows of a CSV - comma, semicolon (Excel in many locales), tab or pipe separated, header first."""
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames or not any(str(f or "").strip() for f in reader.fieldnames):
        raise ValueError("The CSV has no header row")
    return [{k: v for k, v in r.items() if k is not None} for r in reader if any(str(v or "").strip() for k, v in r.items() if k is not None)]


def _rows_for(process: str, text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The file's rows with their columns named as the process names them - and what its header says."""
    raw = parse_csv(text)
    headers = list(raw[0].keys()) if raw else list(csv.DictReader(io.StringIO(text.lstrip("﻿"))).fieldnames or [])
    known = {c for c, _, _ in PROCESS_INFO[process]["columns"]}
    mapped = {h: _column(process, h) for h in headers}
    report = {"columns": [mapped[h] for h in headers if mapped[h] in known],
              "unknown_columns": [h for h in headers if mapped[h] not in known and str(h).strip()],
              "renamed": {h: mapped[h] for h in headers if mapped[h] in known and _key(h) != mapped[h]}}
    return [{mapped[k]: v for k, v in r.items()} for r in raw], report


def preview(db: Session, tenant_id: int, process: str, text: str, defaults: dict[str, Any] | None = None, limit: int = 500) -> dict[str, Any]:
    """What a file would make, row by row, before anything runs: its columns, the rows it can run and why the others can't."""
    if process not in PROCESSES:
        raise ValueError(f"Process: {', '.join(PROCESSES)}")
    rows, report = _rows_for(process, text)
    if not rows:
        raise ValueError("The file has a header but no rows")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"The file has {len(rows)} rows - at most {MAX_ROWS} per bulk order")
    shared = clean_row(process, dict(defaults or {}))
    required = [c for c, r, _ in PROCESS_INFO[process]["columns"] if r]
    out, valid = [], 0
    for n, raw in enumerate(rows, 1):
        data = {**shared, **clean_row(process, raw)}
        why = validate_row(db, tenant_id, process, data)
        valid += not why
        if n <= limit:
            out.append({"row_no": n, "data": data, "valid": not why, "message": why})
    return {**report, "process": process, "process_label": PROCESS_INFO[process]["label"], "rows": len(rows), "valid": valid,
            "invalid": len(rows) - valid, "shown": out,
            "missing_required_columns": [c for c in required if c not in report["columns"] and c not in shared
                                         and not (c == "products" and "products" in shared)]}


def validate_row(db: Session, tenant_id: int, process: str, data: dict[str, Any]) -> str:
    """Why a row can't be run - '' when it can. Light checks; the intents check the rest when they run."""
    from app.models.crm import Party, Quote
    from app.models.fulfillment import Asset

    missing = [c for c, required, _ in PROCESS_INFO[process]["columns"] if required and data.get(c) in (None, "", {})]
    if missing:
        return f"Missing {', '.join(missing)}"
    for c in _INT_COLUMNS:
        if c in data and not isinstance(data[c], int):
            return f"{c} must be a number"
    if process in ("b2c_new_connect", "mobile_lines"):
        if not data.get("party_id") and not data.get("customer_name"):
            return "Give party_id (an existing customer) or customer_name (a new one)"
        if data.get("party_id"):
            party = db.get(Party, data["party_id"])
            if party is None or party.tenant_id != tenant_id:
                return f"Customer {data['party_id']} not found"
    if process == "b2b_quote_order":
        quote = db.get(Quote, data["quote_id"])
        if quote is None or quote.tenant_id != tenant_id:
            return f"Quote {data['quote_id']} not found"
    if process == "asset_change":
        asset = db.get(Asset, data["asset_id"])
        if asset is None or asset.tenant_id != tenant_id:
            return f"Asset {data['asset_id']} not found"
    if process == "mobile_lines" and str(data.get("segment") or "B2C").upper() not in ("B2C", "B2B"):
        return "segment is B2C or B2B"
    return ""


def create(db: Session, tenant_id: int, user, access, data: dict[str, Any]) -> BulkOrder:
    process = str(data.get("process") or "")
    if process not in PROCESSES:
        raise ValueError(f"Process: {', '.join(PROCESSES)}")
    raw_rows = list(data.get("rows") or [])
    if data.get("csv"):
        raw_rows += _rows_for(process, str(data["csv"]))[0]
    if not raw_rows:
        raise ValueError("No rows - give rows or a CSV")
    if len(raw_rows) > MAX_ROWS:
        raise ValueError(f"At most {MAX_ROWS} rows per bulk order")
    defaults = clean_row(process, dict(data.get("defaults") or {}))
    bulk = BulkOrder(tenant_id=tenant_id, name=str(data.get("name") or "")[:200], process=process, status="draft", defaults=defaults,
                     auto_approve=bool(data.get("auto_approve", True)), send_to_fulfillment=bool(data.get("send_to_fulfillment", True)),
                     submitted_by=user.id, position_id=access.active_position.id if access.active_position else None,
                     data_scope=getattr(access, "scope", None), source=str(data.get("source") or ("csv" if data.get("csv") else "api"))[:20])
    db.add(bulk)
    db.flush()
    bulk.number = f"BULK-{bulk.id:06d}"
    bulk.name = bulk.name or f"{PROCESS_INFO[process]['label']} - {len(raw_rows)} row(s)"
    for n, raw in enumerate(raw_rows, 1):
        row_data = {**defaults, **clean_row(process, raw if isinstance(raw, dict) else {})}
        why = validate_row(db, tenant_id, process, row_data)
        db.add(BulkOrderRow(tenant_id=tenant_id, bulk_order_id=bulk.id, row_no=n, data=row_data, stage="invalid" if why else "queued",
                            message=why, order_ids=[], asset_ids=[], billing={}, runs=[], attempts=0))
    db.flush()
    _count(db, bulk)
    if data.get("start", True) and bulk.counts.get("queued"):
        start(db, bulk)
    return bulk


def start(db: Session, bulk: BulkOrder) -> BulkOrder:
    if bulk.status not in ("draft", "completed_with_errors"):
        raise ValueError(f"{bulk.number} is {bulk.status.replace('_', ' ')}")
    bulk.status, bulk.started_at, bulk.finished_at = "running", bulk.started_at or utcnow(), None
    db.flush()
    return bulk


def retry(db: Session, bulk: BulkOrder) -> int:
    """Failed rows (that never made an order) go again; rows stopped in fallout are retried by Fulfillment (INT-118)."""
    rows = db.query(BulkOrderRow).filter(BulkOrderRow.bulk_order_id == bulk.id, BulkOrderRow.stage == "failed").all()
    for row in rows:
        if not row.order_ids:
            row.stage, row.message = "queued", ""
    if rows:
        bulk.status, bulk.finished_at = "running", None
    db.flush()
    _count(db, bulk)
    return sum(1 for r in rows if r.stage == "queued")


def cancel(db: Session, bulk: BulkOrder) -> int:
    """Rows not placed yet are cancelled; orders already made carry on (cancel them as orders)."""
    rows = db.query(BulkOrderRow).filter(BulkOrderRow.bulk_order_id == bulk.id, BulkOrderRow.stage == "queued").all()
    for row in rows:
        row.stage, row.message = "cancelled", "cancelled before it was placed"
    db.flush()
    _count(db, bulk)
    if not any(k not in FINAL_STAGES for k, v in (bulk.counts or {}).items() if v):
        bulk.status, bulk.finished_at = "cancelled", utcnow()
    return len(rows)


# ------------------------------------------------------------------ running a row


def _actor(db: Session, bulk: BulkOrder):
    from app.crm_core import access_service
    from app.models.auth import User

    user = db.get(User, bulk.submitted_by) if bulk.submitted_by else None
    if user is None or not user.is_active:
        raise ValueError("The person who submitted the bulk order can no longer act - submit it again")
    return user, access_service.resolve(db, user, bulk.position_id, bulk.data_scope)


def _run(db: Session, bulk: BulkOrder, row: BulkOrderRow, code: str, entities: dict[str, Any], user, access, order_id: int | None = None) -> Any:
    """One intent for a row, through the intent pipeline as the submitter. The pipeline commits its own run."""
    from app.experience.routes_intent import _handle
    from app.schemas.intent import IntentRequest, StructuredIntent

    if hasattr(access, "_cache"):
        access._cache = {}
    row_id = row.id
    entities = {k: v for k, v in entities.items() if v not in (None, "", [], {})}
    request = IntentRequest(text=f"{bulk.number} row {row.row_no}: {code}", structured=StructuredIntent(intent_type=code, entities=entities))
    response = _handle(request, db, bulk.tenant_id, user, access)
    row = db.get(BulkOrderRow, row_id)
    note = response.rationale if response.status != "executed" else (response.summary or "")
    row.runs = list(row.runs or []) + [{"intent": code, "execution_id": response.intent_execution_id, "status": response.status,
                                        "at": utcnow().isoformat(timespec="seconds"), "note": str(note or "")[:300],
                                        **({"missing": response.missing_fields} if response.missing_fields else {}),
                                        **({"order_id": order_id} if order_id else {})}]
    return response


def _why(response) -> str:
    """Why an intent didn't go through, in its own words (its error, else the reasoning's)."""
    missing = f" (missing {', '.join(response.missing_fields)})" if response.missing_fields else ""
    said = (response.error or {}).get("message") if isinstance(response.error, dict) else None
    return f"{response.intent_label or response.intent_type}: {said or response.rationale or response.status}{missing}"[:500]


def _new_customer(db: Session, bulk: BulkOrder, row: BulkOrderRow, user, access, segment: str = "B2C") -> int | None:
    d = row.data or {}
    if segment.upper() == "B2B":
        response = _run(db, bulk, row, "INT-15", {"segment": "B2B", "name": d.get("customer_name"), "email": d.get("email"),
                                                  "phone": d.get("phone")}, user, access)
    else:
        response = _run(db, bulk, row, "INT-41", {"name": d.get("customer_name"), "email": d.get("email"), "phone": d.get("phone")}, user, access)
    row = db.get(BulkOrderRow, row.id)
    if response.status != "executed":
        row.stage, row.message = "failed", _why(response)
        return None
    row.party_id = (response.result or {}).get("party_id")
    return row.party_id


def _place(db: Session, bulk: BulkOrder, row: BulkOrderRow, user, access) -> None:
    """The row's ordering intent(s): the customer when new, then the order."""
    d = dict(row.data or {})
    row.stage, row.attempts = "ordering", row.attempts + 1
    db.commit()
    party_id = d.get("party_id") or row.party_id
    if bulk.process in ("b2c_new_connect", "mobile_lines") and not party_id:
        party_id = _new_customer(db, bulk, row, user, access, str(d.get("segment") or "B2C"))
        if party_id is None:
            db.commit()
            return
    row = db.get(BulkOrderRow, row.id)
    row.party_id = party_id or row.party_id
    if bulk.process == "b2c_new_connect":
        code, entities = "INT-104", {"party_id": party_id, "products": d.get("products"), "order_channel": d.get("order_channel") or "store",
                                     "installation_address": d.get("installation_address"), "payment_method": d.get("payment_method"),
                                     "channel_reference": d.get("channel_reference") or f"{bulk.number}/{row.row_no}"}
    elif bulk.process == "b2b_quote_order":
        code, entities = "INT-24", {"quote_id": d["quote_id"]}
    elif bulk.process == "mobile_lines":
        code, entities = "INT-136", {"party_id": party_id, **{k: d.get(k) for k in ("offering_name", "quantity", "pricing_attributes", "sim_type", "msisdn",
                                                                                      "iccid", "user_name", "initial_recharge", "payment_method",
                                                                                      "deposit", "credit_limit", "wholesale_partner")}}
    else:
        code, entities = "INT-107", {k: v for k, v in d.items() if k in {c for c, _, _ in PROCESS_INFO["asset_change"]["columns"]}}
    response = _run(db, bulk, row, code, entities, user, access)
    row = db.get(BulkOrderRow, row.id)
    result = response.result or {}
    if response.status != "executed":
        row.stage, row.message = "failed", _why(response)
        db.commit()
        return
    orders = [o["order_id"] for o in result.get("orders") or []] or ([result["order_id"]] if result.get("order_id") else [])
    if not orders:
        row.stage, row.message = "failed", f"{response.intent_label or code} made no order: {response.summary or ''}"[:500]
        db.commit()
        return
    row.order_ids, row.party_id = orders, row.party_id or result.get("party_id")
    row.stage, row.message = "ready_for_approval", ""
    db.commit()


# ------------------------------------------------------------------ following an order to the end


def order_progress(db: Session, order_id: int) -> dict[str, Any]:
    """Where one order is: its stage, its fulfilment order, the assets it installed and what billing
    initiation answered."""
    from app.models.abo import AssetChange, AssetRevision
    from app.models.fulfillment import Asset
    from app.models.fulfillment_orders import FulfillmentOrder, FulfillmentTask
    from app.models.order_mgmt import OmOrderDetail
    from app.models.orders import BillingAccount, Order, OrderStatus

    order = db.get(Order, order_id)
    if order is None:
        return {"order_id": order_id, "stage": "failed", "note": "order not found"}
    items = [i.id for i in order.items] or [-1]
    assets = sorted({r.asset_id for r in db.query(AssetRevision).filter(AssetRevision.order_item_id.in_(items))}
                    | {a.id for a in db.query(Asset).filter(Asset.order_item_id.in_(items))})
    change = db.query(AssetChange).filter(AssetChange.order_id == order.id).first()
    if change is not None:
        assets = sorted(set(assets) | {change.asset_id})
    com = (db.query(FulfillmentOrder).filter(FulfillmentOrder.crm_order_id == order.id, FulfillmentOrder.layer == "COM",
                                             FulfillmentOrder.status != "cancelled").order_by(FulfillmentOrder.id.desc()).first())
    billing: dict[str, Any] = {}
    if com is not None:
        billing = dict(json.loads(com.data_json or "{}").get("billing") or {})
    if not billing.get("billing_start_ref"):
        account = db.query(BillingAccount).filter(BillingAccount.party_id == order.party_id).first()
        if account is not None and getattr(account, "status", "") == "active":
            billing.setdefault("billing_account_id", account.id)
            billing.setdefault("billing_account_status", "active")
    out = {"order_id": order.id, "order_status": order.status.value, "assets": assets, "billing": billing,
           "fulfillment_order": com.number if com else None, "fulfillment_status": com.status if com else None}
    if order.status in (OrderStatus.cancelled, OrderStatus.terminated, OrderStatus.rejected):
        return {**out, "stage": "cancelled", "note": f"order {order.status.value}"}
    done = order.status == OrderStatus.completed or (change is not None and change.status == "completed")
    if done or (com is not None and com.status == "completed"):
        return {**out, "stage": "completed", "note": "fulfilled - " + (f"{len(assets)} asset(s)" if assets else "no asset")}
    if com is not None:
        if com.status == "fallout":
            failed = (db.query(FulfillmentTask).join(FulfillmentOrder, FulfillmentOrder.id == FulfillmentTask.fulfillment_order_id)
                      .filter(FulfillmentOrder.crm_order_id == order.id, FulfillmentTask.status == "fallout").first())
            why = (json.loads(failed.error_json or "{}") if failed and (failed.error_json or "").startswith("{") else {}).get("message") \
                if failed else ""
            return {**out, "stage": "fallout", "note": f"fulfilment fallout at '{failed.label if failed else '?'}'"
                                                         + (f": {why}" if why else "") + " - resolve it (INT-118)"}
        # the step everything waits on: an engineer's work order (TOM) before a service step (SOM) before a route
        rank = {"TOM": 0, "SOM": 1, "COM": 2}
        tasks = (db.query(FulfillmentTask, FulfillmentOrder.layer).join(FulfillmentOrder, FulfillmentOrder.id == FulfillmentTask.fulfillment_order_id)
                 .filter(FulfillmentOrder.crm_order_id == order.id, FulfillmentTask.status == "waiting").all())
        waiting = min(tasks, key=lambda t: (t[0].work_order_id is None, rank.get(t[1], 3)))[0] if tasks else None
        return {**out, "stage": "in_fulfillment", "note": (waiting.note or f"waiting at '{waiting.label}'") if waiting else f"{com.number} in progress"}
    detail = db.query(OmOrderDetail).filter(OmOrderDetail.order_id == order.id).first()
    if detail is not None and detail.om_status == "awaiting_approval":
        return {**out, "stage": "awaiting_discount_approval", "note": f"order {detail.order_number} waits for its discount approval"}
    if detail is not None and detail.om_status == "pending" and order.status == OrderStatus.created:
        return {**out, "stage": "awaiting_order_approval", "note": f"order {detail.order_number} is not submitted - it is on hold or needs attention"}
    if order.status == OrderStatus.created:
        return {**out, "stage": "ready_for_approval", "note": "booked - waiting for Order Control approval"}
    if order.status == OrderStatus.pending_approval:
        return {**out, "stage": "awaiting_order_approval", "note": "waiting for the order approval chain"}
    if change is not None and change.status in ("submitted", "queued"):
        return {**out, "stage": "ready_for_fulfillment", "note": f"change order {change.status}"}
    return {**out, "stage": "in_fulfillment", "note": f"order {order.status.value} - field work and acceptance in progress"}


_ORDER = ("cancelled", "fallout", "failed", "awaiting_discount_approval", "awaiting_order_approval", "ready_for_approval",
          "ready_for_fulfillment", "in_fulfillment", "completed")


def _advance(db: Session, bulk: BulkOrder, row: BulkOrderRow, user, access) -> None:
    """Move a placed row's orders along (approve, send to fulfilment, confirm a change) and record where they are."""
    states = []
    for order_id in list(row.order_ids or []):
        p = order_progress(db, order_id)
        tried = {(r["intent"], r.get("order_id")) for r in row.runs or [] if r.get("order_id")}
        if p["stage"] == "ready_for_approval" and bulk.auto_approve and ("INT-25", order_id) not in tried:
            response = _run(db, bulk, row, "INT-25", {"order_id": order_id, "decision": "approve",
                                                      "reasoning": f"Approved with bulk order {bulk.number}"}, user, access, order_id)
            row = db.get(BulkOrderRow, row.id)
            p = order_progress(db, order_id)
            if response.status != "executed":
                p = {**p, "stage": "awaiting_order_approval", "note": _why(response)}
        if p["stage"] in ("in_fulfillment", "ready_for_fulfillment") and p["fulfillment_order"] is None and bulk.send_to_fulfillment \
                and ("INT-117", order_id) not in tried and _pattern_fits(db, bulk.tenant_id, order_id):
            response = _run(db, bulk, row, "INT-117", {"order_id": order_id}, user, access, order_id)
            row = db.get(BulkOrderRow, row.id)
            p = order_progress(db, order_id) if response.status == "executed" else {**p, "note": _why(response)}
        if p["stage"] == "ready_for_fulfillment" and p["fulfillment_order"] is None and bulk.auto_approve and ("INT-108", order_id) not in tried:
            # a change order no fulfilment pattern takes: confirm its fulfilment, as the single process does (INT-108)
            response = _run(db, bulk, row, "INT-108", {"order_id": order_id}, user, access, order_id)
            row = db.get(BulkOrderRow, row.id)
            p = order_progress(db, order_id) if response.status == "executed" else {**p, "note": _why(response)}
        states.append(p)
    row = db.get(BulkOrderRow, row.id)
    worst = min(states, key=lambda s: _ORDER.index(s["stage"]) if s["stage"] in _ORDER else 0) if states else None
    if worst is None:
        row.stage, row.message = "failed", "no order to follow"
    else:
        row.stage = worst["stage"]
        row.message = worst["note"] if len(states) == 1 else f"{sum(s['stage'] == 'completed' for s in states)}/{len(states)} orders complete - {worst['note']}"
    row.asset_ids = sorted({a for s in states for a in s["assets"]})
    row.billing = {str(s["order_id"]): s["billing"] for s in states if s["billing"]}
    db.commit()


def _pattern_fits(db: Session, tenant_id: int, order_id: int) -> bool:
    from app.models.orders import Order
    from app.orchestration import fulfillment_engine

    order = db.get(Order, order_id)
    return order is not None and fulfillment_engine.matching_pattern(db, tenant_id, order) is not None


def _count(db: Session, bulk: BulkOrder) -> None:
    counts: dict[str, int] = {}
    for (stage,) in db.query(BulkOrderRow.stage).filter(BulkOrderRow.bulk_order_id == bulk.id):
        counts[stage] = counts.get(stage, 0) + 1
    bulk.counts = counts
    if bulk.status == "running" and not any(k not in FINAL_STAGES for k in counts):
        bulk.status = "completed" if set(counts) <= {"completed"} else "completed_with_errors"
        bulk.finished_at = utcnow()


def process(db: Session, bulk_id: int, limit: int = ROWS_PER_TICK) -> dict[str, Any]:
    """One pass over a running request: place up to `limit` queued rows, move every placed row along."""
    from app import brt_id, rls

    bulk = db.get(BulkOrder, bulk_id)
    if bulk is None:
        raise ValueError("Bulk order not found")
    if bulk.status != "running":
        return summary(db, bulk)
    rls.scope(db, bulk.tenant_id)
    marker = brt_id.current_user.set({"user_id": bulk.submitted_by})
    try:
        try:
            user, access = _actor(db, bulk)
        except ValueError as exc:
            for row in db.query(BulkOrderRow).filter(BulkOrderRow.bulk_order_id == bulk.id, BulkOrderRow.stage == "queued"):
                row.stage, row.message = "failed", str(exc)
            _count(db, bulk)
            db.commit()
            return summary(db, bulk)
        queued = [r.id for r in db.query(BulkOrderRow.id).filter(BulkOrderRow.bulk_order_id == bulk.id, BulkOrderRow.stage == "queued")
                  .order_by(BulkOrderRow.row_no).limit(limit)]
        placed = []
        for row_id in queued:
            row = db.get(BulkOrderRow, row_id)
            try:
                _place(db, bulk, row, user, access)
            except Exception as exc:  # noqa: BLE001 - one row's surprise never stops the others
                db.rollback()
                row = db.get(BulkOrderRow, row_id)
                row.stage, row.message = "failed", f"{type(exc).__name__}: {exc}"[:500]
                db.commit()
            placed.append(row_id)
        moving = [r.id for r in db.query(BulkOrderRow.id).filter(
            BulkOrderRow.bulk_order_id == bulk.id, BulkOrderRow.stage.notin_(FINAL_STAGES + ("queued", "ordering")))]
        for row_id in moving:
            row = db.get(BulkOrderRow, row_id)
            try:
                _advance(db, bulk, row, user, access)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                row = db.get(BulkOrderRow, row_id)
                row.message = f"Couldn't check its orders: {type(exc).__name__}: {exc}"[:500]
                db.commit()
        bulk = db.get(BulkOrder, bulk_id)
        _count(db, bulk)
        db.commit()
        return {**summary(db, bulk), "placed_now": len(placed), "followed_now": len(moving)}
    finally:
        brt_id.current_user.reset(marker)


def run_due(session_factory) -> None:
    """Scheduler: every running bulk order, one pass each."""
    from app import rls

    db = session_factory()
    try:
        rls.scope(db, None)
        ids = [i for (i,) in db.query(BulkOrder.id).filter(BulkOrder.status == "running").order_by(BulkOrder.id)]
    finally:
        db.close()
    for bulk_id in ids:
        db = session_factory()
        try:
            process(db, bulk_id)
        except Exception:  # noqa: BLE001 - logged by the scheduler's caller; the next tick tries again
            db.rollback()
        finally:
            db.close()


# ------------------------------------------------------------------ out


def row_out(r: BulkOrderRow) -> dict[str, Any]:
    return {"id": r.id, "row_no": r.row_no, "data": r.data or {}, "stage": r.stage, "party_id": r.party_id, "order_ids": r.order_ids or [],
            "asset_ids": r.asset_ids or [], "billing": r.billing or {}, "runs": r.runs or [], "message": r.message, "attempts": r.attempts,
            "updated_at": r.updated_at}


def summary(db: Session, bulk: BulkOrder) -> dict[str, Any]:
    counts = bulk.counts or {}
    total = sum(counts.values())
    return {"id": bulk.id, "bulk_order_id": bulk.id, "number": bulk.number, "name": bulk.name, "process": bulk.process,
            "process_label": PROCESS_INFO[bulk.process]["label"], "status": bulk.status, "auto_approve": bulk.auto_approve,
            "send_to_fulfillment": bulk.send_to_fulfillment, "defaults": bulk.defaults or {}, "counts": counts, "rows": total,
            "completed": counts.get("completed", 0), "failed": counts.get("failed", 0) + counts.get("invalid", 0),
            "in_progress": total - sum(counts.get(s, 0) for s in FINAL_STAGES), "submitted_by": bulk.submitted_by, "created_at": bulk.created_at,
            "started_at": bulk.started_at, "finished_at": bulk.finished_at}


def detail(db: Session, bulk: BulkOrder, stage: str = "", limit: int = 1000) -> dict[str, Any]:
    q = db.query(BulkOrderRow).filter(BulkOrderRow.bulk_order_id == bulk.id)
    if stage:
        q = q.filter(BulkOrderRow.stage == stage)
    return {**summary(db, bulk), "row_items": [row_out(r) for r in q.order_by(BulkOrderRow.row_no).limit(limit)]}


def results_csv(db: Session, bulk: BulkOrder) -> str:
    buf = io.StringIO()
    columns = [c for c, _, _ in PROCESS_INFO[bulk.process]["columns"]]
    w = csv.writer(buf)
    w.writerow(["row", *columns, "stage", "party_id", "order_ids", "asset_ids", "billing", "message"])
    for r in db.query(BulkOrderRow).filter(BulkOrderRow.bulk_order_id == bulk.id).order_by(BulkOrderRow.row_no):
        d = r.data or {}
        w.writerow([r.row_no, *[json.dumps(d[c]) if isinstance(d.get(c), (dict, list)) else d.get(c, "") for c in columns], r.stage, r.party_id or "",
                    " ".join(str(i) for i in r.order_ids or []), " ".join(str(i) for i in r.asset_ids or []),
                    json.dumps(r.billing or {}) if r.billing else "", r.message])
    return buf.getvalue()


def of(db: Session, tenant_id: int, ref: Any) -> BulkOrder:
    text = str(ref or "").strip()
    q = db.query(BulkOrder).filter(BulkOrder.tenant_id == tenant_id)
    bulk = q.filter(BulkOrder.id == int(text)).first() if text.isdigit() else q.filter(BulkOrder.number.ilike(text)).first()
    if bulk is None and (m := re.search(r"(\d+)$", text)):
        bulk = q.filter(BulkOrder.id == int(m.group(1))).first()
    if bulk is None:
        raise ValueError(f"Bulk order {ref} not found")
    return bulk


def listing(db: Session, tenant_id: int, limit: int = 100) -> list[dict[str, Any]]:
    return [summary(db, b) for b in db.query(BulkOrder).filter(BulkOrder.tenant_id == tenant_id).order_by(BulkOrder.id.desc()).limit(limit)]

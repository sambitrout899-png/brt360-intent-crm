"""BRT_ID - the identity every record in BRT 360 carries.

Format: <ENTITY>-<8 code chars>-<check char>, e.g. PTY-7K3MX9QP-H.

- One global sequence (table brt_id_counter) numbers every record the
  platform ever creates, whatever its table - so no two records anywhere
  share a BRT_ID.
- The number is scrambled by a keyed 4-round Feistel permutation over 40
  bits (a bijection: distinct numbers can never give the same code), so the
  codes reveal neither order nor volume, and can't be predicted without
  the key.
- The check character is a keyed MAC of prefix + code: only BRT 360 (which
  holds the key, settings.brt_id_secret) can mint codes that verify. Data
  arriving through BIM must carry codes BRT 360 issued.

Every ORM record gets its BRT_ID and system fields (created/updated when
and by whom, and the creator's position and department) automatically
when it is first flushed - see install().
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import re
import threading
from typing import Any

from sqlalchemy import Column, Integer, MetaData, String, Table, event, text
from sqlalchemy.orm import Session

from app.config import get_settings

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 - no I, L, O, U
_BITS = 40
_HALF = _BITS // 2
_MASK = (1 << _HALF) - 1
_PATTERN = re.compile(r"^(?P<prefix>[A-Z]{3})-(?P<body>[0-9A-HJKMNP-TV-Z]{8})-(?P<check>[0-9A-HJKMNP-TV-Z])$")

# A readable entity prefix for the main record types; others derive one from the table name.
PREFIXES: dict[str, str] = {
    "tenants": "TEN", "users": "USR", "parties": "PTY", "party_profiles": "PPR", "leads": "LEA", "opportunities": "OPP",
    "opportunity_configs": "OPC", "quotes": "QUO", "quote_items": "QIT", "orders": "ORD", "order_items": "OIT",
    "service_orders": "SOR", "service_accounts": "SAC", "billing_accounts": "BAC", "product_offerings": "PRD",
    "product_specifications": "PSP", "work_orders": "WOR", "trouble_tickets": "TKT", "assets": "AST", "proposals": "PRP",
    "price_lists": "PRL", "price_list_items": "PLI", "positions": "POS", "departments": "DEP", "approval_chain_templates": "APR",
    "approval_chain_instances": "APQ", "flow_definitions": "FLW", "intent_execution_logs": "IEX", "domain_events": "EVT",
    "bim_jobs": "BIJ", "bim_interfaces": "BIF", "page_layouts": "PGL", "validation_rules": "VRL",
    # field service
    "fs_service_regions": "SRG", "fs_engineers": "FSE", "fs_activity_templates": "ATP", "fs_entitlements": "ENT",
    "fs_warranties": "WTY", "fs_asset_warranties": "AWT", "fs_request_details": "SRQ", "fs_activities": "ACT",
    "fs_inventory_locations": "INL", "fs_inventory_levels": "INV", "fs_inventory_transactions": "ITX",
    "fs_pm_plans": "PMP", "fs_pm_actions": "PMA", "fs_repairs": "RPR", "fs_charges": "CHG",
    # order management
    "om_lines": "OLN", "om_shipments": "SHP", "om_payments": "PAY", "om_agreements": "AGR", "om_promotions": "PRM",
    "om_tax_rules": "TAX", "om_order_details": "SOD", "om_quote_details": "QOD", "om_credit_checks": "CRC",
    # communications
    "cm_account_details": "ACD", "cm_contacts": "CON", "cm_billing_profiles": "BPF", "cm_profiles": "PRF", "cm_addresses": "ADR",
    "cm_rating_lists": "RTL", "cm_premises": "PRE", "cm_service_points": "SPT", "cm_invoices": "BIL", "cm_payments": "CPY",
    "cm_adjustment_requests": "ADJ", "cm_payment_arrangements": "PAR", "cm_credit_alerts": "CAL", "cm_fraud_alerts": "FAL",
    "cm_change_requests": "CRQ", "cm_solutions": "SOL", "cm_provider_work_orders": "PWO", "cm_usage": "USG",
    # training (BRD v1.0 tables; the original training tables keep their derived prefixes)
    "training_locations": "TLC", "training_participants": "TPP", "training_certifications": "TCF",
    "training_templates": "TTM", "training_instances": "TAI", "training_anonymous_responses": "TAR",
    "training_attendance": "TAT", "training_qa_reviews": "TQA", "training_outcomes": "TOC",
    "training_notify_templates": "TNT", "training_notifications": "TNO", "training_settings": "TSG",
    "training_audit": "TAD", "training_otps": "TOP",
    "om_channel_interactions": "OCI", "function_map_versions": "FMV",
    "change_workspaces": "CWS", "change_locks": "CLK", "change_revisions": "CRV", "change_releases": "REL",
    # the PDFs of a quote and their templates
    "quote_documents": "QDC", "quote_document_templates": "QDT",
    # inventory
    "inv_item_types": "ITY", "inv_items": "ITM", "inv_relationships": "IRL", "inv_pools": "IPL", "inv_history": "IHS", "inv_reconcile_runs": "IRC",
}

# The register of minted numbers.
counter_table = Table("brt_id_counter", MetaData(), Column("id", Integer, primary_key=True), Column("value", Integer, nullable=False))

# Who is acting - set per request (auth), so records know who created them.
current_user: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("brt_current_user", default=None)
# ...and as which position and department, once the request has worked
# that out (access_service.actor_for) - preferred over current_user.
current_actor: contextvars.ContextVar[dict[str, int | None] | None] = contextvars.ContextVar("current_actor", default=None)


def prefix_for(table: str) -> str:
    if table in PREFIXES:
        return PREFIXES[table]
    letters = "".join(w[0] for w in table.split("_") if w)
    return (letters + re.sub(r"[^a-z]", "", table))[:3].upper().ljust(3, "X")


def _key() -> bytes:
    return get_settings().brt_id_secret.encode()


def _round(value: int, n: int) -> int:
    digest = hmac.new(_key(), f"r{n}:{value}".encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:4], "big") & _MASK


def _permute(number: int) -> int:
    left, right = (number >> _HALF) & _MASK, number & _MASK
    for n in range(4):
        left, right = right, left ^ _round(right, n)
    return (left << _HALF) | right


def _unpermute(value: int) -> int:
    left, right = (value >> _HALF) & _MASK, value & _MASK
    for n in reversed(range(4)):
        left, right = right ^ _round(left, n), left
    return (left << _HALF) | right


def _decode(body: str) -> int:
    value = 0
    for ch in body:
        value = value * 32 + _ALPHABET.index(ch)
    return value


def number_of(brt_id: str) -> int | None:
    """The sequence number behind a valid code (None if not valid)."""
    if not is_valid(brt_id):
        return None
    return _unpermute(_decode(str(brt_id).split("-")[1]))


def counter_top(connection) -> int:
    """The highest number handed out so far."""
    return connection.execute(text("SELECT value FROM brt_id_counter WHERE id = 1")).scalar() or 0


def issued(connection, brt_id: str, table: str | None = None, top: int | None = None) -> bool:
    """Valid, of this table, and actually handed out by the register. `top`:
    counter_top read once for many checks (a BIM load reserves its codes first)."""
    number = number_of(brt_id) if is_valid(brt_id, table) else None
    if number is None:
        return False
    return 0 < number <= (top if top is not None else counter_top(connection))


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def _check(prefix: str, body: str) -> str:
    digest = hmac.new(_key(), f"{prefix}{body}".encode(), hashlib.sha256).digest()
    return _ALPHABET[digest[0] & 31]


def code_for(number: int, table: str) -> str:
    if not 0 < number < (1 << _BITS):
        raise ValueError("BRT_ID sequence out of range")
    prefix = prefix_for(table)
    body = _encode(_permute(number), 8)
    return f"{prefix}-{body}-{_check(prefix, body)}"


def is_valid(brt_id: Any, table: str | None = None) -> bool:
    """True only for a well-formed code with BRT 360's check character (and,
    with table, the right entity prefix)."""
    m = _PATTERN.match(str(brt_id or ""))
    if not m:
        return False
    if table is not None and m.group("prefix") != prefix_for(table):
        return False
    return hmac.compare_digest(m.group("check"), _check(m.group("prefix"), m.group("body")))


def reserve(connection, count: int) -> range:
    """Takes the next `count` numbers from the global sequence."""
    connection.execute(text("UPDATE brt_id_counter SET value = value + :n WHERE id = 1"), {"n": count})
    top = connection.execute(text("SELECT value FROM brt_id_counter WHERE id = 1")).scalar()
    if top is None:
        raise RuntimeError("The BRT_ID register is missing - run the database migrations")
    return range(top - count + 1, top + 1)


def mint(connection, table: str, count: int = 1) -> list[str]:
    return [code_for(n, table) for n in reserve(connection, count)]


# Numbers for records being saved come from a block this process holds. On
# Postgres the block is taken in a transaction of its own, so the counter row
# is locked for that moment only - not for the rest of the request, which made
# every request that saves a record wait for the one before it to commit.
# Numbers a process held but never used are skipped (codes need only be
# unique and issued). SQLite (development, tests) takes them in the session's
# own transaction, as before: its tests share one connection.
BLOCK = 64
_blocks: dict[str, list[int]] = {}
_blocks_lock = threading.Lock()


def _numbers(engine, connection, count: int) -> list[int]:
    if engine.dialect.name != "postgresql":
        return list(reserve(connection, count))
    key = str(engine.url)
    with _blocks_lock:
        held = _blocks.setdefault(key, [])
        if len(held) < count:
            with engine.connect() as own:
                held.extend(reserve(own, max(count - len(held), BLOCK)))
                own.commit()
        taken, held[:] = held[:count], held[count:]
    return taken


# ---------------------------------------------------------------- stamping every record


def _before_flush(session: Session, flush_context, instances) -> None:
    from app.timeutils import utcnow

    # An intent knows the actor's position and department too; any other
    # authenticated request at least who they are.
    actor = _actor()
    now = utcnow()
    fresh = [o for o in session.new if hasattr(o, "brt_id")]
    missing = [o for o in fresh if not o.brt_id]
    if missing:
        connection = session.connection()
        numbers = _numbers(connection.engine, connection, len(missing))
        for obj, number in zip(missing, numbers):
            obj.brt_id = code_for(number, obj.__table__.name)
    for obj in fresh:
        obj.sys_created_at = obj.sys_created_at or now
        if obj.sys_created_by is None:
            obj.sys_created_by = actor.get("user_id")
            obj.sys_created_position_id = actor.get("position_id")
            obj.sys_department_id = actor.get("department_id")
    for obj in session.dirty:
        if hasattr(obj, "brt_id") and _columns_changed(obj):
            obj.sys_updated_at = now
            obj.sys_updated_by = actor.get("user_id")


def _columns_changed(obj) -> bool:
    """One of the record's own columns changed - not just a related record
    attached to it (a new prospect's profile), which isn't an update of it."""
    from sqlalchemy import inspect as sa_inspect

    state = sa_inspect(obj)
    return any(state.attrs[attr.key].history.has_changes() for attr in state.mapper.column_attrs)


def _before_insert(mapper, connection, target) -> None:
    """Safety net for records added while a flush is already under way (by
    another before_flush hook) - they never miss their BRT_ID either."""
    if getattr(target, "brt_id", "x") is None or getattr(target, "brt_id", "x") == "":
        from app.timeutils import utcnow

        target.brt_id = code_for(_numbers(connection.engine, connection, 1)[0], target.__table__.name)
        target.sys_created_at = target.sys_created_at or utcnow()
        if target.sys_created_by is None:
            actor = _actor()
            target.sys_created_by = actor.get("user_id")
            target.sys_created_position_id = actor.get("position_id")
            target.sys_department_id = actor.get("department_id")


def _actor() -> dict[str, Any]:
    return current_actor.get() or current_user.get() or {}


def install() -> None:
    from app.db import Base

    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
    if not event.contains(Base, "before_insert", _before_insert):
        event.listen(Base, "before_insert", _before_insert, propagate=True)


def create_counter(connection) -> None:
    """For databases built from the models (tests): the register, starting at 0."""
    counter_table.create(connection, checkfirst=True)
    if connection.execute(text("SELECT COUNT(*) FROM brt_id_counter")).scalar() == 0:
        connection.execute(text("INSERT INTO brt_id_counter (id, value) VALUES (1, 0)"))

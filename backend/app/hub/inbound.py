"""Other systems calling BRT 360.

A system gets its own key (HubClient), never an administrator's sign-in: revocable
on its own, limited to the services it needs, optionally to its own addresses and a
rate, and its work runs as one chosen user - so that user's position, data scope and
intent permissions apply and the audit trail says who. It presents the key as
X-API-Key, a Bearer token, HTTP Basic, a WS-Security UsernameToken, or trades it for
a short-lived token at the OAuth2 client-credentials endpoint.

What it calls is a published service: an intent or flow - through the same intent
pipeline as a person, with every check and approval - or a BIM interface (each active
interface is published automatically). Synchronously, or asynchronously: 202 and a
correlation id straight away, the work in the queue, the outcome to read back or
sent to a callback. An Idempotency-Key makes a retry return the first answer instead
of doing the work twice.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import brt_id, outbound_guard, ratelimit, rls
from app.auth import create_access_token, decode_token
from app.config import get_settings
from app.hub import mapping, outbound
from app.licensing import require_module
from app.models.auth import User
from app.models.bim import BimInterface
from app.models.integration_hub import HubClient, HubMessage, HubOperation, HubService
from app.timeutils import utcnow

TOKEN_MINUTES = 30
KEY_PREFIX = "brt_"
ROTATION_GRACE = dt.timedelta(days=7)
FAILED_AUTH_PER_MINUTE = 30
MODES = ("sync", "async", "either")


class Refused(Exception):
    """An answer to the caller other than success: (HTTP status, message)."""

    def __init__(self, status: int, message: str, code: str = "", headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.headers = status, message, code, headers or {}


# ---------------------------------------------------------------- keys


def _hash(key: str) -> str:
    return hmac.new(get_settings().brt_id_secret.encode(), key.encode(), hashlib.sha256).hexdigest()


def new_key() -> tuple[str, str]:
    """(key id, the whole key)."""
    key_id = secrets.token_hex(8)
    return key_id, f"{KEY_PREFIX}{key_id}_{secrets.token_urlsafe(32)}"


def issue(row: HubClient) -> str:
    """A new key for a new client. Shown once; only its keyed hash is stored."""
    row.key_id, key = new_key()
    row.key_hash = _hash(key)
    return key


def rotate(row: HubClient) -> str:
    """A new key; the old one keeps working for a week, so callers can move without an outage."""
    key_id = row.key_id
    key = f"{KEY_PREFIX}{key_id}_{secrets.token_urlsafe(32)}"
    row.previous_key_hash, row.previous_key_until = row.key_hash, utcnow() + ROTATION_GRACE
    row.key_hash = _hash(key)
    return key


def _matches(row: HubClient, key: str) -> bool:
    hashed = _hash(key)
    if hmac.compare_digest(hashed, row.key_hash):
        return True
    return bool(row.previous_key_hash and row.previous_key_until and row.previous_key_until > utcnow()
                and hmac.compare_digest(hashed, row.previous_key_hash))


def _key_id_of(key: str) -> str | None:
    if not key.startswith(KEY_PREFIX):
        return None
    return key[len(KEY_PREFIX):].split("_", 1)[0] or None


def _ip_allowed(row: HubClient, ip: str) -> bool:
    entries = [p.strip() for p in (row.ip_allowlist or "").split(",") if p.strip()]
    if not entries:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in entries:
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(headers: Any, fallback: str) -> str:
    """The caller's real address, for allow-lists and limits. X-Forwarded-For is a list anyone can start: each proxy
    only appends what it saw, so only the right-hand end is true. Our proxies are private addresses - the rightmost
    public one is the address that reached us; whatever a caller wrote to its left is never looked at. With nothing
    public in it (a partner on the private network, or no proxy at all) the leftmost, or the connection itself."""
    hops = [h.strip() for h in (headers.get("x-forwarded-for") or "").split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            if ipaddress.ip_address(hop).is_global:
                return hop
        except ValueError:
            continue
    return hops[0] if hops else fallback


@dataclass
class Credentials:
    key: str | None = None
    token_payload: dict[str, Any] | None = None
    supplied: bool = False


def credentials_of(headers: Any, soap_user_token: tuple[str, str] | None = None) -> Credentials:
    """Whichever way the key came."""
    api_key = headers.get("x-api-key")
    if api_key:
        return Credentials(key=api_key.strip(), supplied=True)
    authorization = headers.get("authorization") or ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        if value.startswith(KEY_PREFIX):
            return Credentials(key=value.strip(), supplied=True)
        return Credentials(token_payload=decode_token(value.strip()), supplied=True)
    if scheme.lower() == "basic" and value:
        import base64

        try:
            _user, _, password = base64.b64decode(value).decode().partition(":")
        except ValueError:
            return Credentials(supplied=True)
        return Credentials(key=password, supplied=True)
    if soap_user_token:
        return Credentials(key=soap_user_token[1], supplied=True)
    return Credentials()


def _unauthorized(message: str) -> Refused:
    return Refused(401, message, "unauthorized", {"WWW-Authenticate": 'Basic realm="BRT 360 Integration"'})


def authenticate(db: Session, credentials: Credentials, ip: str) -> HubClient:
    """The client behind these credentials, checked and scoped - or Refused."""
    if not credentials.supplied:
        raise _unauthorized("Send your API key: X-API-Key, Authorization: Bearer, or Basic with the key as the password")
    row = None
    if credentials.key:
        key_id = _key_id_of(credentials.key)
        row = db.query(HubClient).filter(HubClient.key_id == key_id).first() if key_id else None
        if row is not None and not _matches(row, credentials.key):
            row = None
    elif credentials.token_payload:
        purpose = str(credentials.token_payload.get("purpose") or "")
        if purpose.startswith("integration:"):
            row = db.query(HubClient).filter(HubClient.key_id == purpose.split(":", 1)[1]).first()
    if row is None:
        if not ratelimit.allow(f"hub-auth-fail:{ip}", FAILED_AUTH_PER_MINUTE):
            raise Refused(429, "Too many failed attempts - wait a minute", "rate_limited", {"Retry-After": "60"})
        raise _unauthorized("That key isn't valid")
    if not row.is_active:
        raise _unauthorized("This client has been switched off")
    if row.expires_at and row.expires_at < utcnow():
        raise _unauthorized("This key has expired")
    if not _ip_allowed(row, ip):
        raise Refused(403, f"Calls from {ip} are not allowed for this client", "forbidden")
    if not ratelimit.allow(f"hub-client:{row.id}", row.rate_per_minute or 0):
        raise Refused(429, f"Over this client's limit of {row.rate_per_minute} calls a minute", "rate_limited", {"Retry-After": "60"})
    rls.scope(db, row.tenant_id)
    if row.last_used_at is None or utcnow() - row.last_used_at > dt.timedelta(minutes=1):
        row.last_used_at = utcnow()
        db.commit()
        rls.scope(db, row.tenant_id)
    return row


def issue_token(db: Session, key_id: str, secret: str) -> tuple[str, int] | None:
    """OAuth2 client credentials: (access token, seconds it lasts), or None."""
    row = db.query(HubClient).filter(HubClient.key_id == key_id).first() if key_id else None
    if row is None or not row.is_active or not _matches(row, secret) or (row.expires_at and row.expires_at < utcnow()):
        return None
    user = db.get(User, row.run_as_user_id)
    if user is None or not user.is_active:
        return None
    return create_access_token(user, purpose=f"integration:{row.key_id}", minutes=TOKEN_MINUTES), TOKEN_MINUTES * 60


def allowed_services(row: HubClient) -> list[str]:
    try:
        found = json.loads(row.allowed_services_json or "[]")
    except ValueError:
        return []
    return [str(s) for s in found] if isinstance(found, list) else []


def may_call(row: HubClient, code: str) -> bool:
    allowed = allowed_services(row)
    return "*" in allowed or code in allowed


# ---------------------------------------------------------------- published services


@dataclass
class Service:
    code: str
    name: str
    description: str
    target_type: str
    target: str
    mode: str = "either"
    fields: list[dict[str, Any]] = field(default_factory=list)
    input_mapping: Any = None
    output_mapping: Any = None
    callback_operation_id: int | None = None
    id: int | None = None
    published_from_interface: bool = False


def _fields_of_interface(db: Session, tenant_id: int, interface: BimInterface) -> list[dict[str, Any]]:
    from app.crm_core import bim_service

    spec = next((e for e in bim_service.entities() if e["key"] == interface.entity), None)
    if spec is None:
        return []
    source_of = _sources(mapping.load_mapping(interface.mapping_json))
    fields = []
    if interface.key_field:
        fields.append({"name": interface.key_field, "type": "string", "required": True, "description": "This system's own id of the record"})
    for column in spec["columns"]:
        name = source_of.get(column["name"], column["name"])
        if column["name"] == "brt_id" and interface.key_field:
            continue  # BRT 360 issues it
        if "." in name or "[" in name or name == interface.key_field:
            continue
        fields.append({"name": name, "type": "string", "required": bool(column["required"]), "description": column.get("help") or ""})
    return fields


def _from_interface(db: Session, tenant_id: int, row: BimInterface) -> Service:
    return Service(code=row.code, name=row.name, description=row.description or f"Load {row.entity} records through BIM", target_type="bim",
                   target=row.code, fields=_fields_of_interface(db, tenant_id, row), published_from_interface=True)


def _sources(rules: Any) -> dict[str, str]:
    """our field name -> the name the caller uses for it."""
    if isinstance(rules, dict):
        return {str(v).strip().lower(): str(k) for k, v in rules.items()}
    return {str(r.get("target") or "").strip().lower(): str(r.get("source") or "") for r in rules or [] if r.get("source")}


def _from_row(db: Session, tenant_id: int, row: HubService) -> Service:
    fields = _load_list(row.fields_json)
    if row.target_type == "action":
        from app.intent_engine import intent_registry

        # What the action needs, as the contract's fields. Not marked required: the pipeline can often work a
        # value out from the record named (a customer from an order), and answers 422 with missing_fields if not.
        # Only a field the administrator declares required is refused up front.
        sources = _sources(mapping.load_mapping(row.input_mapping_json))
        named = {str(f.get("name")) for f in fields}
        needed = [sources.get(str(r).lower(), r) for r in intent_registry.requirements(db, tenant_id, row.target)[0]]
        fields = [{"name": r, "type": "string", "required": False, "description": "Needed by this action, unless it can be worked out from the record named"}
                  for r in needed if r not in named] + fields
    elif not fields:
        interface = db.query(BimInterface).filter(BimInterface.tenant_id == tenant_id, BimInterface.code == row.target).first()
        fields = _fields_of_interface(db, tenant_id, interface) if interface else []
    return Service(code=row.code, name=row.name, description=row.description, target_type=row.target_type, target=row.target, mode=row.mode,
                   fields=fields, input_mapping=mapping.load_mapping(row.input_mapping_json), output_mapping=mapping.load_mapping(row.output_mapping_json),
                   callback_operation_id=row.callback_operation_id, id=row.id)


def _load_list(raw: str | None) -> list[dict[str, Any]]:
    try:
        found = json.loads(raw or "[]")
    except ValueError:
        return []
    return [f for f in found if isinstance(f, dict) and f.get("name")] if isinstance(found, list) else []


def list_services(db: Session, tenant_id: int, client: HubClient | None = None) -> list[Service]:
    rows = db.query(HubService).filter(HubService.tenant_id == tenant_id, HubService.is_active.is_(True)).order_by(HubService.code).all()
    out = [_from_row(db, tenant_id, r) for r in rows]
    taken = {s.code for s in out}
    interfaces = db.query(BimInterface).filter(BimInterface.tenant_id == tenant_id, BimInterface.is_active.is_(True)).order_by(BimInterface.code).all()
    out += [_from_interface(db, tenant_id, i) for i in interfaces if i.code not in taken]
    return [s for s in out if client is None or may_call(client, s.code)]


def find_service(db: Session, tenant_id: int, code: str) -> Service | None:
    code = str(code or "").strip().upper()
    row = db.query(HubService).filter(HubService.tenant_id == tenant_id, HubService.code == code).first()
    if row is not None:
        return _from_row(db, tenant_id, row) if row.is_active else None
    interface = db.query(BimInterface).filter(BimInterface.tenant_id == tenant_id, BimInterface.code == code, BimInterface.is_active.is_(True)).first()
    return _from_interface(db, tenant_id, interface) if interface else None


# ---------------------------------------------------------------- doing the work


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _records(payload: Any) -> list[dict[str, Any]]:
    """One record, a list, {"records": [...]} - or what SOAP's repeated element makes of them."""
    if isinstance(payload, dict):
        for key in ("records", "record"):
            if key in payload:
                inner = payload[key]
                if isinstance(inner, dict) and "record" in inner:
                    inner = inner["record"]
                return _records(inner)
        return [payload]
    if isinstance(payload, list) and all(isinstance(r, dict) for r in payload):
        return payload
    raise Refused(400, 'Send a record, a list of records, or {"records": [...]}', "invalid_request")


def _run_bim(db: Session, tenant_id: int, service: Service, payload: Any, user: User, dry_run: bool, message_id: int | None = None) -> tuple[int, dict[str, Any]]:
    from app.crm_core import bim_service

    require_module(db, tenant_id, "telecom_crm")
    interface = db.query(BimInterface).filter(BimInterface.tenant_id == tenant_id, BimInterface.code == service.target, BimInterface.is_active.is_(True)).first()
    if interface is None:
        raise Refused(404, f"Interface {service.target} is not available", "not_found")
    rows = _records(payload)
    try:
        job, results = bim_service.run(db, tenant_id, interface.entity, rows, "api", interface, None, dry_run, True, user.id)
    except ValueError as exc:
        db.rollback()
        raise Refused(400, str(exc), "invalid_request") from exc
    job.message_id = message_id
    body = {"status": job.status, "job_id": job.id, "job": job.brt_id, "dry_run": dry_run, "total": job.total, "inserted": job.inserted,
            "updated": job.updated, "failed": job.failed, "results": results[:2000]}
    db.commit()
    return (200 if job.failed == 0 else 207 if job.failed < job.total else 422), body


def _run_action(db: Session, tenant_id: int, service: Service, entities: dict[str, Any], user: User, dry_run: bool) -> tuple[int, dict[str, Any]]:
    from app.crm_core import access_service
    from app.experience.routes_intent import _handle
    from app.orchestration import rehearsal
    from app.schemas.intent import IntentRequest, StructuredIntent

    entities = {k: v for k, v in entities.items() if v not in (None, "", [], {})}
    access = access_service.resolve(db, user)
    request = IntentRequest(text=f"[{service.code}] via integration", channel="integration",
                            structured=StructuredIntent(intent_type=service.target, entities=entities))
    if dry_run:
        with outbound_guard.simulation(), rehearsal.capture(db) as rows:
            response = _handle(request, db, tenant_id, user, access, rehearse=rows)
        db.rollback()
    else:
        response = _handle(request, db, tenant_id, user, access)
    result = _jsonable(response.result) if response.result else {}
    if service.output_mapping and result:
        result = _jsonable(mapping.apply(result, service.output_mapping, keep_unmapped=False))
    body: dict[str, Any] = {"status": response.status, "summary": response.summary or "", "result": result, "intent": response.intent_type,
                            "execution_id": response.intent_execution_id or None, "dry_run": dry_run}
    done = response.status in ("executed", "rehearsed")  # rehearsed: a dry run that would have gone through
    if not done:
        said = response.error.get("message") if isinstance(getattr(response, "error", None), dict) else None
        body["message"] = said or response.rationale
        body["missing_fields"] = response.missing_fields
    return (200 if done else 422), body


def execute(db: Session, tenant_id: int, service: Service, payload: Any, run_as_id: int, dry_run: bool = False, message_id: int | None = None) -> tuple[int, dict[str, Any]]:
    """Does the service's work as the client's user; (HTTP status, body)."""
    user = db.get(User, run_as_id)
    if user is None or not user.is_active or user.tenant_id != tenant_id:
        raise Refused(403, "The user this client runs as is no longer active - choose another in Integration Hub -> Inbound", "forbidden")
    marker = brt_id.current_user.set({"user_id": user.id})
    try:
        rls.scope(db, tenant_id)
        if service.target_type == "bim":
            return _run_bim(db, tenant_id, service, payload, user, dry_run, message_id)
        if not isinstance(payload, dict):
            raise Refused(400, "This service takes one JSON object", "invalid_request")
        try:
            entities = mapping.apply(payload, service.input_mapping) if service.input_mapping else dict(payload)
        except ValueError as exc:
            raise Refused(400, str(exc), "invalid_request") from exc
        missing = [f["name"] for f in service.fields if f.get("required") and mapping.dig(payload, str(f["name"])) in (None, "")
                   and entities.get(str(f["name"]).lower()) in (None, "")]
        if missing:
            raise Refused(400, f"Missing required field(s): {', '.join(missing)}", "invalid_request")
        return _run_action(db, tenant_id, service, entities, user, dry_run)
    except HTTPException as exc:  # a licence or permission check deep inside
        raise Refused(exc.status_code, str(exc.detail), "forbidden" if exc.status_code == 403 else "invalid_request") from exc
    finally:
        brt_id.current_user.reset(marker)


@dataclass
class Answer:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


def _stored_answer(msg: HubMessage) -> Answer:
    try:
        body = json.loads(msg.reply_json or "{}")
    except ValueError:
        body = {}
    return Answer(msg.http_status or 200, body, {"Idempotent-Replayed": "true", "X-Correlation-ID": msg.correlation_id})


def invoke(db: Session, client: HubClient, code: str, payload: Any, *, wants_async: bool = False, idempotency_key: str | None = None,
           correlation_id: str | None = None, protocol: str = "rest", dry_run: bool = False, http_method: str = "POST", url: str = "",
           headers: dict[str, str] | None = None) -> Answer:
    """One inbound call, from authenticated client to answer."""
    tenant_id = client.tenant_id
    if not may_call(client, str(code or "").strip().upper()):
        raise Refused(403, f"This client may not call {code}", "forbidden")
    service = find_service(db, tenant_id, code)
    if service is None:
        raise Refused(404, f"There is no service {code}", "not_found")
    if wants_async and service.mode == "sync":
        raise Refused(400, f"{service.code} only answers synchronously", "invalid_request")
    run_async = (wants_async or service.mode == "async") and not dry_run
    key = (idempotency_key or "").strip()[:200] or None
    if key:
        earlier = db.query(HubMessage).filter(HubMessage.client_id == client.id, HubMessage.service_code == service.code,
                                              HubMessage.idempotency_key == key).first()
        if earlier is not None:
            if earlier.status in ("queued", "processing") and not earlier.mode == "async":
                raise Refused(409, "The first request with this Idempotency-Key is still running", "conflict")
            if earlier.status in ("queued", "processing"):
                return Answer(202, _accepted_body(earlier), {"Idempotent-Replayed": "true", "X-Correlation-ID": earlier.correlation_id})
            return _stored_answer(earlier)
    client_id, run_as_id = client.id, client.run_as_user_id
    msg = HubMessage(tenant_id=tenant_id, correlation_id=(correlation_id or "").strip()[:64] or outbound.new_correlation_id(), direction="in", kind="service",
                     protocol=protocol, mode="async" if run_async else "sync", status="queued" if run_async else "processing", client_id=client_id,
                     service_code=service.code, idempotency_key=None if dry_run else key, http_method=http_method, url=url[:1000],
                     request_headers=json.dumps(outbound.masked_headers(headers or {})), payload=json.dumps(payload, default=str),
                     next_attempt_at=utcnow() if run_async else None, source=f"client:{client.code}"[:100], max_attempts=1)
    db.add(msg)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # a retry raced the first request
        raise Refused(409, "The first request with this Idempotency-Key is still running", "conflict") from None
    rls.scope(db, tenant_id)
    message_id, correlation = msg.id, msg.correlation_id
    if run_async:
        return Answer(202, _accepted_body(msg), {"X-Correlation-ID": correlation, "Location": f"/api/gateway/messages/{correlation}"})
    started = utcnow()
    try:
        status, body = execute(db, tenant_id, service, payload, run_as_id, dry_run, message_id)
    except Refused as refused:
        db.rollback()
        rls.scope(db, tenant_id)
        _finish(db, message_id, refused.status, {"status": "refused", "message": refused.message, "correlation_id": correlation}, "failed", refused.message, started)
        raise
    except Exception as exc:  # noqa: BLE001 - the log records it; the caller gets a reference, not internals
        db.rollback()
        rls.scope(db, tenant_id)
        _finish(db, message_id, 500, {"status": "failed", "correlation_id": correlation}, "failed", f"{type(exc).__name__}: {exc}", started)
        raise Refused(500, f"The service could not complete - quote {correlation}", "server_error") from exc
    body = {**body, "correlation_id": correlation, "service": service.code}
    rls.scope(db, tenant_id)
    _finish(db, message_id, status, body, "succeeded" if status < 300 else "failed", body.get("message", "") if status >= 300 else "", started)
    return Answer(status, body, {"X-Correlation-ID": correlation})


def _accepted_body(msg: HubMessage) -> dict[str, Any]:
    return {"status": "accepted", "correlation_id": msg.correlation_id, "service": msg.service_code,
            "status_url": f"/api/gateway/messages/{msg.correlation_id}"}


def _finish(db: Session, message_id: int, status: int, body: dict[str, Any], outcome: str, error: str, started: dt.datetime) -> None:
    msg = db.get(HubMessage, message_id)
    msg.http_status, msg.status, msg.finished_at, msg.locked_until = status, outcome, utcnow(), None
    msg.reply_json = json.dumps(_jsonable(body))
    msg.response_body = msg.reply_json[:outbound.MAX_STORED]
    msg.error, msg.latency_ms = str(error or "")[:2000], int((utcnow() - started).total_seconds() * 1000)
    db.commit()


def process_queued(db: Session, msg: HubMessage) -> None:
    """The worker's side of an async call. Sets the outcome; the caller commits."""
    message_id, tenant_id, correlation, code = msg.id, msg.tenant_id, msg.correlation_id, msg.service_code
    client = db.get(HubClient, msg.client_id) if msg.client_id else None
    service = find_service(db, tenant_id, code or "")
    started = utcnow()
    if client is None or service is None:
        _finish(db, message_id, 404, {"status": "failed", "correlation_id": correlation, "message": f"Service {code} is no longer available"}, "failed",
                f"Service {code} is no longer available", started)
        return
    run_as_id = client.run_as_user_id
    msg.attempts += 1
    try:
        payload = json.loads(msg.payload or "null")
    except ValueError:
        payload = None
    try:
        status, body = execute(db, tenant_id, service, payload, run_as_id, False, message_id)
    except Refused as refused:
        db.rollback()
        status, body = refused.status, {"status": "refused", "message": refused.message}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        status, body = 500, {"status": "failed", "message": f"The service could not complete - quote {correlation}"}
        msg_error = f"{type(exc).__name__}: {exc}"
        rls.scope(db, tenant_id)
        _finish(db, message_id, status, {**body, "correlation_id": correlation, "service": service.code}, "failed", msg_error, started)
        _notify_caller(db, tenant_id, service, correlation, "failed", body)
        return
    body = {**body, "correlation_id": correlation, "service": service.code}
    rls.scope(db, tenant_id)
    outcome = "succeeded" if status < 300 else "failed"
    _finish(db, message_id, status, body, outcome, body.get("message", "") if status >= 300 else "", started)
    _notify_caller(db, tenant_id, service, correlation, outcome, body)


def _notify_caller(db: Session, tenant_id: int, service: Service, correlation: str, outcome: str, body: dict[str, Any]) -> None:
    """Async work ended: the service's callback operation is told - queued like any other call, so it retries."""
    if not service.callback_operation_id:
        return
    rls.scope(db, tenant_id)
    op = db.get(HubOperation, service.callback_operation_id)
    conn = db.get(outbound.HubConnection, op.connection_id) if op else None
    if op is None or conn is None:
        return
    msg = outbound.enqueue(db, tenant_id, op, conn, {"correlation_id": correlation, "service": service.code, "outcome": outcome, "result": body},
                           kind="ack", source=f"service:{service.code}"[:100])
    msg.mode = "async"
    db.commit()


def status_of(db: Session, client: HubClient, correlation_id: str) -> dict[str, Any] | None:
    """A client's own call, by correlation id."""
    msg = db.query(HubMessage).filter(HubMessage.tenant_id == client.tenant_id, HubMessage.client_id == client.id,
                                      HubMessage.correlation_id == correlation_id).order_by(HubMessage.id.desc()).first()
    if msg is None:
        return None
    out: dict[str, Any] = {"correlation_id": msg.correlation_id, "service": msg.service_code, "status": msg.status, "created_at": msg.created_at,
                           "finished_at": msg.finished_at, "http_status": msg.http_status}
    if msg.reply_json:
        try:
            out["result"] = json.loads(msg.reply_json)
        except ValueError:
            pass
    return out

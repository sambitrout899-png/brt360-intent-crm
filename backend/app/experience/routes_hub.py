"""Integration Hub administration (app/hub/): who may call us, what we publish, whom we
call, and every message either way. Tenant administrators only."""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.db import get_db
from app.hub import contracts, inbound, mapping, outbound, worker
from app.models.auth import User, UserRole
from app.models.bim import BimInterface
from app.models.integration_hub import HubClient, HubConnection, HubMessage, HubOperation, HubService, HubSubscription
from app.models.tenant import Tenant
from app.tenancy import get_current_tenant_id
from app.timeutils import utcnow

router = APIRouter(prefix="/api/integration", tags=["integration hub"], dependencies=[Depends(require_roles(UserRole.admin))])


def _code(raw: str) -> str:
    code = re.sub(r"[^A-Za-z0-9_-]", "_", raw.strip()).upper()
    if not re.match(r"[A-Z_]", code):
        raise HTTPException(400, "A code starts with a letter")
    return code


def _own(db: Session, model, row_id: int, tenant_id: int, what: str):
    row = db.get(model, row_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, f"{what} not found")
    return row


def _clash(db: Session, model, tenant_id: int, code: str, row_id: int | None, what: str) -> None:
    if db.query(model.id).filter(model.tenant_id == tenant_id, model.code == code, model.id != (row_id or 0)).first():
        raise HTTPException(400, f"There is already {what} {code}")


def _object(value: Any, what: str) -> str:
    if value in (None, "", {}):
        return "{}"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise HTTPException(400, f"{what} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, f"{what} must be a JSON object")
    return json.dumps(value)


def _mapping_text(value: Any, what: str) -> str:
    if value in (None, "", {}, []):
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise HTTPException(400, f"{what} must be JSON") from exc
    try:
        return json.dumps(mapping.valid_mapping(value))
    except ValueError as exc:
        raise HTTPException(400, f"{what}: {exc}") from exc


# ---------------------------------------------------------------- overview


@router.get("/overview")
def overview(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    since = utcnow() - dt.timedelta(hours=24)
    rows = db.query(HubMessage.direction, HubMessage.status, func.count(HubMessage.id)).filter(
        HubMessage.tenant_id == tenant_id, HubMessage.created_at >= since).group_by(HubMessage.direction, HubMessage.status).all()
    counts: dict[str, dict[str, int]] = {"in": {}, "out": {}}
    for direction, status, count in rows:
        counts.setdefault(direction, {})[status] = count
    dead = db.query(func.count(HubMessage.id)).filter(HubMessage.tenant_id == tenant_id, HubMessage.status == "dead").scalar() or 0
    queued = db.query(func.count(HubMessage.id)).filter(HubMessage.tenant_id == tenant_id, HubMessage.status.in_(("queued", "sending", "processing"))).scalar() or 0
    waiting = db.query(func.count(HubMessage.id)).filter(HubMessage.tenant_id == tenant_id, HubMessage.status == "awaiting_reply").scalar() or 0
    latency = db.query(func.avg(HubMessage.latency_ms)).filter(HubMessage.tenant_id == tenant_id, HubMessage.created_at >= since, HubMessage.latency_ms.isnot(None)).scalar()
    now = utcnow()
    tripped = [{"code": c.code, "name": c.name, "until": c.circuit_open_until} for c in db.query(HubConnection).filter(
        HubConnection.tenant_id == tenant_id, HubConnection.circuit_open_until > now)]
    return {"last_24h": counts, "dead_letters": dead, "in_queue": queued, "awaiting_reply": waiting, "avg_latency_ms": round(latency) if latency else None,
            "tripped_connections": tripped,
            "totals": {"clients": db.query(func.count(HubClient.id)).filter(HubClient.tenant_id == tenant_id, HubClient.is_active.is_(True)).scalar(),
                       "connections": db.query(func.count(HubConnection.id)).filter(HubConnection.tenant_id == tenant_id).scalar(),
                       "operations": db.query(func.count(HubOperation.id)).filter(HubOperation.tenant_id == tenant_id).scalar(),
                       "services": len(inbound.list_services(db, tenant_id)),
                       "subscriptions": db.query(func.count(HubSubscription.id)).filter(HubSubscription.tenant_id == tenant_id).scalar()}}


# ---------------------------------------------------------------- clients (inbound)


class ClientIn(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    run_as_user_id: int
    allowed_services: list[str] = ["*"]
    ip_allowlist: str = ""
    rate_per_minute: int = Field(default=600, ge=0, le=100000)
    expires_at: dt.datetime | None = None
    is_active: bool = True


def _client_out(row: HubClient) -> dict[str, Any]:
    return {"id": row.id, "brt_id": row.brt_id, "code": row.code, "name": row.name, "key_id": row.key_id, "run_as_user_id": row.run_as_user_id,
            "allowed_services": inbound.allowed_services(row), "ip_allowlist": row.ip_allowlist, "rate_per_minute": row.rate_per_minute,
            "expires_at": row.expires_at, "is_active": row.is_active, "last_used_at": row.last_used_at, "created_at": row.created_at,
            "rotating_until": row.previous_key_until if row.previous_key_until and row.previous_key_until > utcnow() else None}


def _save_client(db: Session, tenant_id: int, row: HubClient, payload: ClientIn) -> None:
    code = _code(payload.code)
    _clash(db, HubClient, tenant_id, code, row.id, "a client")
    user = db.get(User, payload.run_as_user_id)
    if user is None or user.tenant_id != tenant_id or not user.is_active:
        raise HTTPException(400, "Choose an active user of your organization for it to run as")
    for entry in filter(None, (p.strip() for p in payload.ip_allowlist.split(","))):
        try:
            import ipaddress

            ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            raise HTTPException(400, f"'{entry}' is not an address or network") from exc
    row.code, row.name, row.run_as_user_id = code, payload.name, payload.run_as_user_id
    row.allowed_services_json = json.dumps([s.strip().upper() if s.strip() != "*" else "*" for s in payload.allowed_services if s.strip()])
    row.ip_allowlist, row.rate_per_minute, row.expires_at, row.is_active = payload.ip_allowlist, payload.rate_per_minute, payload.expires_at, payload.is_active


@router.get("/clients")
def clients(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [_client_out(r) for r in db.query(HubClient).filter(HubClient.tenant_id == tenant_id).order_by(HubClient.code)]


@router.post("/clients")
def add_client(payload: ClientIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """The answer holds the key. It is shown this once - only its hash is kept."""
    row = HubClient(tenant_id=tenant_id)
    _save_client(db, tenant_id, row, payload)
    key = inbound.issue(row)
    db.add(row)
    db.commit()
    return {**_client_out(row), "key": key}


@router.put("/clients/{client_id}")
def update_client(client_id: int, payload: ClientIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _own(db, HubClient, client_id, tenant_id, "Client")
    _save_client(db, tenant_id, row, payload)
    db.commit()
    return _client_out(row)


@router.post("/clients/{client_id}/rotate")
def rotate_client(client_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """A new key, shown once. The old one keeps working for 7 days so the caller can move over without an outage."""
    row = _own(db, HubClient, client_id, tenant_id, "Client")
    key = inbound.rotate(row)
    db.commit()
    return {**_client_out(row), "key": key}


@router.post("/clients/{client_id}/revoke-previous")
def revoke_previous(client_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _own(db, HubClient, client_id, tenant_id, "Client")
    row.previous_key_hash, row.previous_key_until = None, None
    db.commit()
    return _client_out(row)


# ---------------------------------------------------------------- services (inbound)


class ServiceIn(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    target_type: str = "action"
    target: str = Field(min_length=1, max_length=60)
    mode: str = "either"
    fields: list[dict[str, Any]] = []
    input_mapping: Any = None
    output_mapping: Any = None
    callback_operation_id: int | None = None
    is_active: bool = True


def _service_out(row: HubService) -> dict[str, Any]:
    return {"id": row.id, "brt_id": row.brt_id, "code": row.code, "name": row.name, "description": row.description, "target_type": row.target_type,
            "target": row.target, "mode": row.mode, "fields": json.loads(row.fields_json or "[]"), "input_mapping": mapping.load_mapping(row.input_mapping_json) or None,
            "output_mapping": mapping.load_mapping(row.output_mapping_json) or None, "callback_operation_id": row.callback_operation_id, "is_active": row.is_active}


def _save_service(db: Session, tenant_id: int, row: HubService, payload: ServiceIn) -> None:
    code = _code(payload.code)
    _clash(db, HubService, tenant_id, code, row.id, "a service")
    if payload.target_type not in ("action", "bim"):
        raise HTTPException(400, "A service runs an action (an intent or flow) or a BIM interface")
    if payload.mode not in inbound.MODES:
        raise HTTPException(400, f"Mode is one of {', '.join(inbound.MODES)}")
    target = payload.target.strip().upper()
    if payload.target_type == "bim":
        if not db.query(BimInterface.id).filter(BimInterface.tenant_id == tenant_id, BimInterface.code == target).first():
            raise HTTPException(400, f"There is no BIM interface {target}")
    else:
        from app import capabilities
        from app.orchestration import flow_engine

        if not (capabilities.has_steps(target) or flow_engine.flow_from_intent_code(db, tenant_id, target)):
            raise HTTPException(400, f"There is no action {target} - use an intent code (INT-15) or a flow's code (FLOW-12)")
    if payload.callback_operation_id:
        _own(db, HubOperation, payload.callback_operation_id, tenant_id, "Callback operation")
    for f in payload.fields:
        if not str(f.get("name") or "").strip():
            raise HTTPException(400, "Every published field needs a name")
    row.code, row.name, row.description, row.target_type, row.target, row.mode = code, payload.name, payload.description, payload.target_type, target, payload.mode
    row.fields_json = json.dumps(payload.fields)
    row.input_mapping_json = _mapping_text(payload.input_mapping, "Input mapping")
    row.output_mapping_json = _mapping_text(payload.output_mapping, "Output mapping")
    row.callback_operation_id, row.is_active = payload.callback_operation_id, payload.is_active


@router.get("/services")
def services(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """What is published: services set up here, and each active BIM interface, which publishes itself."""
    rows = db.query(HubService).filter(HubService.tenant_id == tenant_id).order_by(HubService.code).all()
    return {"services": [_service_out(r) for r in rows],
            "published": [{"code": s.code, "name": s.name, "description": s.description, "target_type": s.target_type, "target": s.target, "mode": s.mode,
                           "fields": s.fields, "from_interface": s.published_from_interface} for s in inbound.list_services(db, tenant_id)]}


@router.post("/services")
def add_service(payload: ServiceIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = HubService(tenant_id=tenant_id)
    _save_service(db, tenant_id, row, payload)
    db.add(row)
    db.commit()
    return _service_out(row)


@router.put("/services/{service_id}")
def update_service(service_id: int, payload: ServiceIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _own(db, HubService, service_id, tenant_id, "Service")
    _save_service(db, tenant_id, row, payload)
    db.commit()
    return _service_out(row)


class TryIn(BaseModel):
    code: str
    payload: Any = {}


@router.post("/services-try")
def try_service(body: TryIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                user: User = Depends(get_current_user)) -> dict[str, Any]:
    """The admin's console: a dry run as this administrator - everything checked, nothing kept, nothing sent out."""
    service = inbound.find_service(db, tenant_id, body.code)
    if service is None:
        raise HTTPException(404, "Service not found")
    try:
        status, answer = inbound.execute(db, tenant_id, service, body.payload, user.id, dry_run=True)
    except inbound.Refused as refused:
        db.rollback()
        return {"http_status": refused.status, "answer": {"error": refused.code, "message": refused.message}}
    return {"http_status": status, "answer": json.loads(json.dumps(answer, default=str))}


class MappingIn(BaseModel):
    mapping: Any = None
    sample: Any = {}
    keep_unmapped: bool = True


@router.post("/mapping/preview")
def preview_mapping(body: MappingIn) -> dict[str, Any]:
    """What a mapping makes of a sample record, before it is saved."""
    if not isinstance(body.sample, dict):
        raise HTTPException(400, "The sample must be one JSON object")
    try:
        rules = mapping.valid_mapping(body.mapping or {})
        return {"ok": True, "result": json.loads(json.dumps(mapping.apply(body.sample, rules, body.keep_unmapped), default=str))}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/targets")
def targets(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """What a service can run, what a client can run as, and the transforms a mapping can use."""
    from app.intent_engine import intent_registry
    from app.models.flows import FlowDefinition

    actions = [{"code": i["code"], "label": i.get("label") or i["code"]} for i in intent_registry.all_intents(db, tenant_id) if i.get("is_active")]
    known = {a["code"] for a in actions}
    flows = [{"code": f"FLOW-{f.id}", "label": f.name} for f in db.query(FlowDefinition).filter(
        FlowDefinition.tenant_id == tenant_id, FlowDefinition.trigger_type == "custom_intent").order_by(FlowDefinition.name) if f"FLOW-{f.id}" not in known]
    users = [{"id": u.id, "name": u.name, "email": u.email, "role": u.role.value} for u in db.query(User).filter(
        User.tenant_id == tenant_id, User.is_active.is_(True)).order_by(User.name).limit(500)]
    interfaces = [{"id": i.id, "code": i.code, "name": i.name, "entity": i.entity} for i in db.query(BimInterface).filter(BimInterface.tenant_id == tenant_id).order_by(BimInterface.code)]
    return {"actions": actions + flows, "users": users, "interfaces": interfaces, "transforms": list(mapping.TRANSFORMS),
            "events": _event_types(db, tenant_id), "auth_types": list(outbound.AUTH_TYPES)}


def _event_types(db: Session, tenant_id: int) -> list[str]:
    from app.models.intent_log import DomainEvent, IntentExecutionLog

    seen = [row[0] for row in db.query(DomainEvent.event_type).join(IntentExecutionLog, IntentExecutionLog.id == DomainEvent.correlation_id).filter(
        IntentExecutionLog.tenant_id == tenant_id).distinct().limit(300)]
    return sorted(set(seen))


# ---------------------------------------------------------------- contracts


def _tenant_contract_title(tenant: Tenant) -> str:
    return f"{tenant.display_name or tenant.name} - BRT 360 integration"


def _origin(request: Request) -> str:
    from app.experience.routes_gateway import origin_of

    return origin_of(request)


@router.get("/contracts/openapi.json")
def contract_openapi(request: Request, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    tenant = db.get(Tenant, tenant_id)
    return JSONResponse(contracts.openapi(inbound.list_services(db, tenant_id), _origin(request), _tenant_contract_title(tenant)),
                        headers={"Content-Disposition": 'attachment; filename="brt360-integration-openapi.json"'})


@router.get("/contracts/service.wsdl")
def contract_wsdl(request: Request, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    tenant = db.get(Tenant, tenant_id)
    document = contracts.wsdl(inbound.list_services(db, tenant_id), tenant.slug, f"{_origin(request)}/api/gateway/soap", _tenant_contract_title(tenant))
    return Response(document, media_type="text/xml; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="brt360-integration.wsdl"'})


# ---------------------------------------------------------------- connections (outbound)


class ConnectionIn(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    base_url: str = Field(default="", max_length=500)
    auth_type: str = "none"
    auth_header: str = Field(default="", max_length=100)
    username: str = Field(default="", max_length=200)
    secret: str | None = None  # blank on an update keeps the one held
    token_url: str = Field(default="", max_length=500)
    oauth_scope: str = Field(default="", max_length=300)
    headers: Any = None
    timeout_seconds: int = Field(default=15, ge=1, le=outbound.MAX_ASYNC_TIMEOUT)
    is_active: bool = True


def _connection_out(row: HubConnection) -> dict[str, Any]:
    secret = row.secret or ""
    return {"id": row.id, "brt_id": row.brt_id, "code": row.code, "name": row.name, "base_url": row.base_url, "auth_type": row.auth_type,
            "auth_header": row.auth_header, "username": row.username, "has_secret": bool(secret), "secret_hint": ("•" * 8 + secret[-4:]) if len(secret) > 8 else ("•" * len(secret)),
            "token_url": row.token_url, "oauth_scope": row.oauth_scope, "headers": json.loads(row.headers_json or "{}"), "timeout_seconds": row.timeout_seconds,
            "is_active": row.is_active, "failure_count": row.failure_count,
            "circuit_open_until": row.circuit_open_until if row.circuit_open_until and row.circuit_open_until > utcnow() else None}


def _check_address(url: str, what: str) -> None:
    from urllib.parse import urlsplit

    if url:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise HTTPException(400, f"{what} must start with http:// or https:// and name a host")
        if parts.username or parts.password:
            raise HTTPException(400, f"Credentials don't go in {what.lower()} - use the connection's fields")


def _save_connection(db: Session, tenant_id: int, row: HubConnection, payload: ConnectionIn) -> None:
    code = _code(payload.code)
    _clash(db, HubConnection, tenant_id, code, row.id, "a connection")
    if payload.auth_type not in outbound.AUTH_TYPES:
        raise HTTPException(400, f"Authentication is one of {', '.join(outbound.AUTH_TYPES)}")
    _check_address(payload.base_url, "The address")
    _check_address(payload.token_url, "The token address")
    if payload.auth_type == "oauth2" and not (payload.token_url and payload.username):
        raise HTTPException(400, "OAuth2 needs a token address and a client id")
    row.code, row.name, row.base_url, row.auth_type, row.auth_header = code, payload.name, payload.base_url.strip(), payload.auth_type, payload.auth_header
    row.username, row.token_url, row.oauth_scope = payload.username, payload.token_url.strip(), payload.oauth_scope
    row.headers_json, row.timeout_seconds, row.is_active = _object(payload.headers, "Headers"), payload.timeout_seconds, payload.is_active
    if payload.secret:
        row.secret = payload.secret


@router.get("/connections")
def connections(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [_connection_out(r) for r in db.query(HubConnection).filter(HubConnection.tenant_id == tenant_id).order_by(HubConnection.code)]


@router.post("/connections")
def add_connection(payload: ConnectionIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = HubConnection(tenant_id=tenant_id, failure_count=0)
    _save_connection(db, tenant_id, row, payload)
    db.add(row)
    db.commit()
    return _connection_out(row)


@router.put("/connections/{connection_id}")
def update_connection(connection_id: int, payload: ConnectionIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _own(db, HubConnection, connection_id, tenant_id, "Connection")
    _save_connection(db, tenant_id, row, payload)
    db.commit()
    return _connection_out(row)


@router.post("/connections/{connection_id}/reset-breaker")
def reset_breaker(connection_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _own(db, HubConnection, connection_id, tenant_id, "Connection")
    row.failure_count, row.circuit_open_until = 0, None
    db.commit()
    return _connection_out(row)


# ---------------------------------------------------------------- operations (outbound)


class OperationIn(BaseModel):
    connection_id: int
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    protocol: str = "rest"
    method: str = "POST"
    path: str = Field(default="", max_length=500)
    headers: Any = None
    body_template: str = ""
    soap_action: str = Field(default="", max_length=300)
    soap_version: str = "1.1"
    pattern: str = "request_reply"
    mode: str = "sync"
    reply_via: str = "response"
    max_attempts: int = Field(default=5, ge=1, le=20)
    backoff_seconds: int = Field(default=30, ge=1, le=3600)
    reply_timeout_minutes: int = Field(default=1440, ge=1, le=43200)
    reply_mapping: Any = None
    reply_interface_id: int | None = None
    is_active: bool = True


def _operation_out(db: Session, row: HubOperation) -> dict[str, Any]:
    conn = db.get(HubConnection, row.connection_id)
    return {"id": row.id, "brt_id": row.brt_id, "connection_id": row.connection_id, "connection": conn.code if conn else None, "code": row.code, "name": row.name,
            "description": row.description, "protocol": row.protocol, "method": row.method, "path": row.path, "headers": json.loads(row.headers_json or "{}"),
            "body_template": row.body_template, "soap_action": row.soap_action, "soap_version": row.soap_version, "pattern": row.pattern, "mode": row.mode,
            "reply_via": row.reply_via, "max_attempts": row.max_attempts, "backoff_seconds": row.backoff_seconds, "reply_timeout_minutes": row.reply_timeout_minutes,
            "reply_mapping": mapping.load_mapping(row.reply_mapping_json) or None, "reply_interface_id": row.reply_interface_id, "is_active": row.is_active}


def _save_operation(db: Session, tenant_id: int, row: HubOperation, payload: OperationIn) -> None:
    code = _code(payload.code)
    _clash(db, HubOperation, tenant_id, code, row.id, "an operation")
    _own(db, HubConnection, payload.connection_id, tenant_id, "Connection")
    if payload.protocol not in ("rest", "soap"):
        raise HTTPException(400, "Protocol is rest or soap")
    method = payload.method.strip().upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise HTTPException(400, "Method is GET, POST, PUT, PATCH or DELETE")
    if payload.pattern not in ("request_reply", "request_only"):
        raise HTTPException(400, "Pattern is request_reply or request_only")
    if payload.mode not in ("sync", "async"):
        raise HTTPException(400, "Mode is sync or async")
    if payload.reply_via not in ("response", "callback"):
        raise HTTPException(400, "A reply comes as the response or by callback")
    if payload.reply_via == "callback" and (payload.mode != "async" or payload.pattern != "request_reply"):
        raise HTTPException(400, "A reply by callback needs an asynchronous request-reply operation")
    if payload.soap_version not in ("1.1", "1.2"):
        raise HTTPException(400, "SOAP version is 1.1 or 1.2")
    if payload.reply_interface_id:
        _own(db, BimInterface, payload.reply_interface_id, tenant_id, "BIM interface")
    row.connection_id, row.code, row.name, row.description, row.protocol, row.method = payload.connection_id, code, payload.name, payload.description, payload.protocol, method
    row.path, row.headers_json, row.body_template = payload.path.strip(), _object(payload.headers, "Headers"), payload.body_template
    row.soap_action, row.soap_version, row.pattern, row.mode, row.reply_via = payload.soap_action, payload.soap_version, payload.pattern, payload.mode, payload.reply_via
    row.max_attempts, row.backoff_seconds, row.reply_timeout_minutes = payload.max_attempts, payload.backoff_seconds, payload.reply_timeout_minutes
    row.reply_mapping_json = _mapping_text(payload.reply_mapping, "Reply mapping")
    row.reply_interface_id, row.is_active = payload.reply_interface_id, payload.is_active


@router.get("/operations")
def operations(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [_operation_out(db, r) for r in db.query(HubOperation).filter(HubOperation.tenant_id == tenant_id).order_by(HubOperation.code)]


@router.post("/operations")
def add_operation(payload: OperationIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = HubOperation(tenant_id=tenant_id)
    _save_operation(db, tenant_id, row, payload)
    db.add(row)
    db.commit()
    return _operation_out(db, row)


@router.put("/operations/{operation_id}")
def update_operation(operation_id: int, payload: OperationIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _own(db, HubOperation, operation_id, tenant_id, "Operation")
    _save_operation(db, tenant_id, row, payload)
    db.commit()
    return _operation_out(db, row)


class CallIn(BaseModel):
    payload: Any = {}


@router.post("/operations/{operation_id}/test")
def test_operation(operation_id: int, body: CallIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """A real call, now, whatever the operation's mode - with the whole exchange shown. Not retried, not queued."""
    row = _own(db, HubOperation, operation_id, tenant_id, "Operation")
    conn = db.get(HubConnection, row.connection_id)
    out = outbound.test_call(db, tenant_id, row, conn, body.payload)
    db.commit()
    return json.loads(json.dumps(out, default=str))


@router.post("/operations/{operation_id}/send")
def send_operation(operation_id: int, body: CallIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Runs the operation the way it is set up (sync answers, async queues) and logs it - as a flow step would."""
    row = _own(db, HubOperation, operation_id, tenant_id, "Operation")
    try:
        out = outbound.call(db, tenant_id, row.code, body.payload, source="console")
    except outbound.NotConfigured as problem:
        raise HTTPException(400, str(problem)) from problem
    except outbound.CallFailed as failure:
        db.commit()
        return {"status": "failed", "error": failure.message, "http_status": failure.status}
    db.commit()
    return json.loads(json.dumps(out, default=str))


# ---------------------------------------------------------------- subscriptions (webhooks)


class SubscriptionIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    event_pattern: str = Field(min_length=1, max_length=150)
    operation_id: int
    is_active: bool = True


def _subscription_out(db: Session, row: HubSubscription) -> dict[str, Any]:
    op = db.get(HubOperation, row.operation_id)
    sent = db.query(func.count(HubMessage.id)).filter(HubMessage.subscription_id == row.id).scalar() or 0
    return {"id": row.id, "brt_id": row.brt_id, "name": row.name, "event_pattern": row.event_pattern, "operation_id": row.operation_id,
            "operation": op.code if op else None, "is_active": row.is_active, "messages": sent}


@router.get("/subscriptions")
def subscriptions(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [_subscription_out(db, r) for r in db.query(HubSubscription).filter(HubSubscription.tenant_id == tenant_id).order_by(HubSubscription.name)]


@router.post("/subscriptions")
def add_subscription(payload: SubscriptionIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Events from now on - not the ones already in the outbox."""
    _own(db, HubOperation, payload.operation_id, tenant_id, "Operation")
    top = worker.current_event_id(db)
    row = HubSubscription(tenant_id=tenant_id, name=payload.name, event_pattern=payload.event_pattern.strip(), operation_id=payload.operation_id,
                          is_active=payload.is_active, start_event_id=top, last_event_id=top)
    db.add(row)
    db.commit()
    return _subscription_out(db, row)


@router.put("/subscriptions/{subscription_id}")
def update_subscription(subscription_id: int, payload: SubscriptionIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _own(db, HubSubscription, subscription_id, tenant_id, "Subscription")
    _own(db, HubOperation, payload.operation_id, tenant_id, "Operation")
    if payload.is_active and not row.is_active:  # switched back on: from now, not everything it missed
        row.last_event_id = row.start_event_id = worker.current_event_id(db)
    row.name, row.event_pattern, row.operation_id, row.is_active = payload.name, payload.event_pattern.strip(), payload.operation_id, payload.is_active
    db.commit()
    return _subscription_out(db, row)


# ---------------------------------------------------------------- the message log


def _message_out(db: Session, row: HubMessage, full: bool = False) -> dict[str, Any]:
    out = {"id": row.id, "correlation_id": row.correlation_id, "direction": row.direction, "kind": row.kind, "protocol": row.protocol, "pattern": row.pattern,
           "mode": row.mode, "status": row.status, "attempts": row.attempts, "max_attempts": row.max_attempts, "next_attempt_at": row.next_attempt_at,
           "service_code": row.service_code, "source": row.source, "http_method": row.http_method, "url": row.url, "http_status": row.http_status,
           "latency_ms": row.latency_ms, "error": row.error, "created_at": row.created_at, "finished_at": row.finished_at, "reply_deadline": row.reply_deadline}
    if row.operation_id:
        op = db.get(HubOperation, row.operation_id)
        out["operation"] = op.code if op else None
    if row.client_id:
        client = db.get(HubClient, row.client_id)
        out["client"] = client.code if client else None
    if full:
        out.update(payload=row.payload, request_headers=json.loads(row.request_headers or "{}"), request_body=row.request_body,
                   response_body=row.response_body, reply=row.reply_json, execution_id=row.execution_id, flow_run_id=row.flow_run_id)
    return out


@router.get("/messages")
def messages(direction: str | None = None, status: str | None = None, q: str = "", limit: int = Query(100, ge=1, le=500), before_id: int | None = None,
             db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    query = db.query(HubMessage).filter(HubMessage.tenant_id == tenant_id)
    if direction:
        query = query.filter(HubMessage.direction == direction)
    if status:
        query = query.filter(HubMessage.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(HubMessage.correlation_id.ilike(like) | HubMessage.service_code.ilike(like) | HubMessage.source.ilike(like)
                             | HubMessage.url.ilike(like) | HubMessage.error.ilike(like))
    if before_id:
        query = query.filter(HubMessage.id < before_id)
    return [_message_out(db, r) for r in query.order_by(HubMessage.id.desc()).limit(limit)]


@router.get("/messages/{message_id}")
def message_detail(message_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return _message_out(db, _own(db, HubMessage, message_id, tenant_id, "Message"), full=True)


@router.post("/messages/{message_id}/replay")
def replay(message_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Sends a failed, dead or timed-out outbound message again; runs an inbound one again. A new try, keeping its correlation id."""
    row = _own(db, HubMessage, message_id, tenant_id, "Message")
    if row.status in ("queued", "sending", "processing"):
        raise HTTPException(400, "This message is already on its way")
    if row.direction == "in" and row.mode != "async":
        raise HTTPException(400, "A synchronous inbound call was answered at the time - its caller decides whether to send it again")
    if row.flow_run_id and row.status == "dead":
        from app.models.flows import FlowRun

        run = db.get(FlowRun, row.flow_run_id)
        if run is not None and run.status != "waiting":
            row.flow_run_id = None  # its flow went on down "failed": this try no longer steers it
    if row.direction == "out" and not row.operation_id:
        raise HTTPException(400, "This message has no operation to send it through")
    row.status, row.attempts, row.error, row.next_attempt_at, row.locked_until, row.finished_at = "queued", 0, "", utcnow(), None, None
    row.reply_token_hash, row.reply_deadline = None, None
    row.max_attempts = max(row.max_attempts, 1)
    db.commit()
    return _message_out(db, row)


@router.post("/messages/{message_id}/cancel")
def cancel(message_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _own(db, HubMessage, message_id, tenant_id, "Message")
    if row.status not in ("queued", "awaiting_reply", "dead"):
        raise HTTPException(400, f"A message that is {row.status} can't be cancelled")
    row.status, row.finished_at, row.reply_token_hash = "cancelled", utcnow(), None
    db.commit()
    return _message_out(db, row)

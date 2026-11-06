"""The integration gateway: what other systems call (app/hub/inbound.py).

    REST    POST /api/gateway/services/{code}     Prefer: respond-async, Idempotency-Key, ?dry_run=true
    SOAP    POST /api/gateway/soap                WSDL at GET /api/gateway/soap?wsdl
    OpenAPI GET  /api/gateway/openapi.json
    Status  GET  /api/gateway/messages/{correlation_id}
    Token   POST /api/gateway/oauth/token         OAuth2 client credentials
    EAI     GET  /api/gateway/eai/{code}/changes  the change feed; /records/{brt_id}; /xref/... for ids

No session here: callers bring a client's key. POST /callback/{token} is the one exception:
its long random token is the credential (security_catalog.PUBLIC_BY_DESIGN).
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app import ratelimit
from app.config import get_settings
from app.db import get_db
from app.hub import contracts, inbound, mapping, worker
from app.hub.inbound import Answer, Refused
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/gateway", tags=["integration gateway"])

MAX_BODY_BYTES = 10_000_000
LOGGED_HEADERS = ("content-type", "user-agent", "x-correlation-id", "idempotency-key", "prefer", "soapaction")
JSON_HEADERS = {"Cache-Control": "no-store"}


def _ip(request: Request) -> str:
    return inbound.client_ip(request.headers, request.client.host if request.client else "?")


def origin_of(request: Request) -> str:
    settings = get_settings()
    configured = (settings.integration_public_origin or settings.training_public_origin or "").rstrip("/")
    return configured or str(request.base_url).rstrip("/")


async def _read(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise Refused(413, f"The body is larger than {MAX_BODY_BYTES // 1_000_000} MB - split it or load it as a file through BIM", "too_large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise Refused(413, f"The body is larger than {MAX_BODY_BYTES // 1_000_000} MB - split it or load it as a file through BIM", "too_large")
    return body


def _refused(refused: Refused, correlation_id: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": refused.code or "refused", "message": refused.message}
    if correlation_id:
        body["correlation_id"] = correlation_id
    return JSONResponse(body, status_code=refused.status, headers=refused.headers)


def _answer(answer: Answer) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(answer.body, default=str)), status_code=answer.status, headers=answer.headers)


def _client(db: Session, request: Request, user_token: tuple[str, str] | None = None):
    return inbound.authenticate(db, inbound.credentials_of(request.headers, user_token), _ip(request))


def _logged_headers(request: Request) -> dict[str, str]:
    return {k: request.headers[k] for k in LOGGED_HEADERS if k in request.headers}


def _payload_of(body: bytes, content_type: str) -> Any:
    """JSON; XML; or a CSV file's rows for a BIM interface."""
    text = body.decode("utf-8-sig", "replace").strip()
    if not text:
        return {}
    if "xml" in content_type or text.startswith("<"):
        try:
            found = mapping.to_dict(mapping.parse_xml(body))
        except ValueError as exc:
            raise Refused(400, str(exc), "invalid_request") from exc
        return found if isinstance(found, dict) else {}
    if "csv" in content_type:
        from app.crm_core import bim_service

        try:
            return bim_service.parse(text, "csv")
        except ValueError as exc:
            raise Refused(400, str(exc), "invalid_request") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Refused(400, "The body isn't valid JSON", "invalid_request") from exc


# ---------------------------------------------------------------- OAuth2


@router.post("/oauth/token")
async def oauth_token(request: Request, db: Session = Depends(get_db)) -> Response:
    """OAuth2 client credentials (RFC 6749 section 4.4). client_id is the key id, client_secret the key."""
    raw = await request.body()

    def work() -> Response:
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        client_id, secret = form.get("client_id", ""), form.get("client_secret", "")
        scheme, _, value = (request.headers.get("authorization") or "").partition(" ")
        if scheme.lower() == "basic" and value:
            try:
                client_id, _, secret = base64.b64decode(value).decode().partition(":")
            except ValueError:
                pass
        if form.get("grant_type") != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400, headers=JSON_HEADERS)
        if not ratelimit.allow(f"hub-token:{_ip(request)}", 60):
            return JSONResponse({"error": "slow_down"}, status_code=429, headers={"Retry-After": "60"})
        issued = inbound.issue_token(db, client_id, secret)
        if issued is None:
            return JSONResponse({"error": "invalid_client"}, status_code=401, headers={"WWW-Authenticate": 'Basic realm="BRT 360 Integration"'})
        return JSONResponse({"access_token": issued[0], "token_type": "Bearer", "expires_in": issued[1]}, headers=JSON_HEADERS)

    return await run_in_threadpool(work)


# ---------------------------------------------------------------- REST


@router.get("/ping")
def ping(request: Request, db: Session = Depends(get_db)) -> Response:
    try:
        client = _client(db, request)
    except Refused as refused:
        return _refused(refused)
    return JSONResponse({"ok": True, "client": client.code, "services": [s.code for s in inbound.list_services(db, client.tenant_id, client)]})


@router.get("/services")
def services(request: Request, db: Session = Depends(get_db)) -> Response:
    try:
        client = _client(db, request)
    except Refused as refused:
        return _refused(refused)
    return JSONResponse([{"code": s.code, "name": s.name, "description": s.description, "mode": s.mode, "fields": s.fields}
                         for s in inbound.list_services(db, client.tenant_id, client)])


@router.post("/services/{code}")
async def invoke(code: str, request: Request, dry_run: bool = False, db: Session = Depends(get_db)) -> Response:
    """Calls a published service. Prefer: respond-async answers 202 and does the work in the queue."""
    headers = request.headers

    def work(body: bytes) -> Response:
        try:
            client = _client(db, request)
            payload = _payload_of(body, headers.get("content-type", ""))
            return _answer(inbound.invoke(db, client, code, payload, wants_async="respond-async" in headers.get("prefer", "").lower(),
                                          idempotency_key=headers.get("idempotency-key"), correlation_id=headers.get("x-correlation-id"),
                                          dry_run=dry_run, url=request.url.path, headers=_logged_headers(request)))
        except Refused as refused:
            return _refused(refused)

    try:
        body = await _read(request)
    except Refused as refused:
        return _refused(refused)
    return await run_in_threadpool(work, body)


@router.get("/messages/{correlation_id}")
def message(correlation_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    try:
        client = _client(db, request)
    except Refused as refused:
        return _refused(refused)
    found = inbound.status_of(db, client, correlation_id)
    if found is None:
        return JSONResponse({"error": "not_found", "message": "No call of yours has that correlation id"}, status_code=404)
    return JSONResponse(json.loads(json.dumps(found, default=str)))


@router.get("/openapi.json")
def openapi_document(request: Request, db: Session = Depends(get_db)) -> Response:
    try:
        client = _client(db, request)
    except Refused as refused:
        return _refused(refused)
    tenant = db.get(Tenant, client.tenant_id)
    return JSONResponse(contracts.openapi(inbound.list_services(db, client.tenant_id, client), origin_of(request),
                                          f"{tenant.display_name or tenant.name} - BRT 360 integration API"))


# ---------------------------------------------------------------- SOAP


@router.get("/soap")
def soap_contract(request: Request, db: Session = Depends(get_db)) -> Response:
    """The WSDL (?wsdl). Toolkits fetch it with HTTP Basic: the key id and the key."""
    try:
        client = _client(db, request)
    except Refused as refused:
        return _refused(refused)
    if "wsdl" not in {k.lower() for k in request.query_params}:
        return JSONResponse({"error": "invalid_request", "message": "Ask for ?wsdl, or POST a SOAP envelope"}, status_code=400)
    tenant = db.get(Tenant, client.tenant_id)
    document = contracts.wsdl(inbound.list_services(db, client.tenant_id, client), tenant.slug, f"{origin_of(request)}/api/gateway/soap",
                              f"{tenant.display_name or tenant.name} integration")
    return Response(document, media_type="text/xml; charset=utf-8")


@router.post("/soap")
async def soap_call(request: Request, db: Session = Depends(get_db)) -> Response:
    """One SOAP call. A problem comes back as a SOAP fault - HTTP 500 for 1.1, as its specification says; 400 / 500 for 1.2."""
    version = "1.2" if "soap+xml" in request.headers.get("content-type", "") else "1.1"

    def fault_response(refused: Refused, used: str) -> Response:
        status = 401 if refused.status == 401 else (500 if used == "1.1" or refused.status >= 500 else 400)
        return Response(contracts.fault(used, refused.code or "Client", refused.message), status_code=status,
                        media_type=contracts.content_type(used), headers=refused.headers)

    try:
        body = await _read(request)
    except Refused as refused:
        return fault_response(refused, version)

    def work() -> Response:
        used = version
        try:
            # the body is read for a key only when the headers bring none: XML from someone unknown is parsed as little as possible
            has_header_key = inbound.credentials_of(request.headers).supplied
            client = _client(db, request, None if has_header_key else contracts.parse_user_token(body))
            call = contracts.parse_request(body)
            used = call.version
            tenant = db.get(Tenant, client.tenant_id)
            slug = tenant.slug
            service = contracts.service_for(inbound.list_services(db, client.tenant_id, client), call.operation)
            if service is None:
                raise Refused(404, f"This client has no operation {call.operation}", "not_found")
            payload = dict(call.payload)
            mode = str(payload.pop("callMode", "") or "").lower()
            key = payload.pop("idempotencyKey", None) or request.headers.get("idempotency-key")
            answer = inbound.invoke(db, client, service.code, payload, wants_async=mode == "async" or "respond-async" in request.headers.get("prefer", "").lower(),
                                    idempotency_key=key, correlation_id=request.headers.get("x-correlation-id"), protocol="soap", url=request.url.path,
                                    headers=_logged_headers(request))
            if answer.status >= 400:
                detail = answer.body.get("message") or answer.body.get("status") or "Refused"
                raise Refused(answer.status, f"{detail} (correlation id {answer.body.get('correlation_id')})", "Client" if answer.status < 500 else "Server")
            # always 200: in SOAP's HTTP binding a response with a body is a 200, and toolkits read anything else as a fault.
            # "Accepted for later" is in the body: status=accepted, httpStatus=202.
            return Response(contracts.response(used, slug, call.operation, answer.status, answer.body), media_type=contracts.content_type(used),
                            status_code=200, headers=answer.headers)
        except Refused as refused:
            return fault_response(refused, used)

    return await run_in_threadpool(work)


# ---------------------------------------------------------------- async replies


@router.post("/callback/{token}")
async def callback(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """Another system's reply to something we sent it asynchronously - the {{reply_url}} it was given."""
    if not ratelimit.allow(f"hub-callback:{_ip(request)}", 600):
        return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"})
    try:
        body = await _read(request)
    except Refused as refused:
        return _refused(refused)

    def work() -> Response:
        status, answer = worker.callback(db, token, body, request.headers.get("content-type", ""))
        return JSONResponse(answer, status_code=status)

    return await run_in_threadpool(work)


# ---------------------------------------------------------------- EAI: reading back


def _eai_interface(db: Session, client, code: str):
    from app.models.bim import BimInterface

    code = code.strip().upper()
    if not inbound.may_call(client, code):
        raise Refused(403, f"This client may not use {code}", "forbidden")
    row = db.query(BimInterface).filter(BimInterface.tenant_id == client.tenant_id, BimInterface.code == code, BimInterface.is_active.is_(True)).first()
    if row is None:
        raise Refused(404, f"There is no interface {code}", "not_found")
    return row


@router.get("/eai/{code}/changes")
def eai_changes(code: str, request: Request, since: str = "", limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)) -> Response:
    """The interface's records created or changed since a cursor, oldest first. Follow next_cursor until has_more is false."""
    from app.crm_core import bim_service

    try:
        client = _client(db, request)
        interface = _eai_interface(db, client, code)
        return JSONResponse(json.loads(json.dumps(bim_service.changes(db, client.tenant_id, interface, since, limit), default=str)))
    except Refused as refused:
        return _refused(refused)
    except ValueError as exc:
        return _refused(Refused(400, str(exc), "invalid_request"))


@router.post("/eai/{code}/brt-ids")
def eai_reserve(code: str, request: Request, count: int = Query(1, ge=1, le=10000), db: Session = Depends(get_db)) -> Response:
    """BRT_IDs in advance, for a source that wants to hold them itself. An interface with a key field doesn't need these."""
    from app.crm_core import bim_service

    try:
        client = _client(db, request)
        interface = _eai_interface(db, client, code)
    except Refused as refused:
        return _refused(refused)
    ids = bim_service.reserve_ids(db, interface.entity, count)
    db.commit()
    return JSONResponse({"entity": interface.entity, "brt_ids": ids})


@router.get("/records/{code}")
def record(code: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """Any loadable record by its BRT_ID. A client limited to certain services needs an interface for that entity among them."""
    from app.crm_core import bim_service
    from app.models.bim import BimInterface

    try:
        client = _client(db, request)
    except Refused as refused:
        return _refused(refused)
    found = bim_service.record_by_brt_id(db, client.tenant_id, code)
    if found is not None and "*" not in inbound.allowed_services(client):
        allowed = {i.entity for i in db.query(BimInterface).filter(BimInterface.tenant_id == client.tenant_id,
                                                                   BimInterface.code.in_(inbound.allowed_services(client)))}
        if found.get("entity") not in allowed:
            found = None
    if found is None:
        return JSONResponse({"error": "not_found", "message": "No record of yours with that BRT_ID"}, status_code=404)
    return JSONResponse(json.loads(json.dumps(found, default=str)))


@router.get("/xref/{system}/{entity}/{external_id}")
def xref(system: str, entity: str, external_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """Your id -> the BRT_ID BRT 360 gave that record."""
    from app.crm_core import bim_service
    from app.models.bim import BimInterface

    try:
        client = _client(db, request)
    except Refused as refused:
        return _refused(refused)
    if "*" not in inbound.allowed_services(client):
        allowed = {i.entity for i in db.query(BimInterface).filter(BimInterface.tenant_id == client.tenant_id,
                                                                   BimInterface.code.in_(inbound.allowed_services(client)))}
        if entity not in allowed:
            return JSONResponse({"error": "not_found", "message": "No such id"}, status_code=404)
    found = bim_service.external_ref(db, client.tenant_id, system, entity, external_id)
    if found is None:
        return JSONResponse({"error": "not_found", "message": "No such id"}, status_code=404)
    return JSONResponse({"system": system.upper(), "entity": entity, "external_id": external_id, "brt_id": found})

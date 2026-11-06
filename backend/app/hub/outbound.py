"""BRT 360 calling other systems.

An operation (models/integration_hub.py) is one of four kinds:

    request-reply  sync    call, wait, use the answer                     (a credit check inside a flow)
    request-only   sync    call, wait for "accepted", no answer kept      (a must-land notification)
    request-only   async   queued in the same transaction, sent after it  (a webhook, an event to the ERP)
    request-reply  async   queued; the reply is the HTTP answer, or comes
                           later to {{reply_url}} ("callback")            (provisioning that takes hours)

Async work is a transactional outbox: the message is a row in the same transaction
as the business change that caused it, so a rolled-back order sends nothing and a
committed one can't lose its message. The worker (hub/worker.py) sends it, with
exponential backoff and a dead-letter queue, and every call carries one stable
correlation id, sent as Idempotency-Key, so a retry can't be applied twice by a
receiver that honours it.

The network wall: only http(s), never a redirect, never the cloud metadata service,
and no private, loopback or link-local address unless the platform allows that
range (INTEGRATION_ALLOW_PRIVATE_CIDRS, for on-premise systems) - a tenant's
administrator must not be able to reach this server's own database or containers.
The address is checked and then pinned, so DNS can't change between check and call.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import logging
import random
import re
import secrets
import socket
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import escape

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import outbound_guard
from app.config import get_settings
from app.hub import mapping
from app.models.integration_hub import HubConnection, HubMessage, HubOperation
from app.timeutils import utcnow

log = logging.getLogger("brt360.integration")

AUTH_TYPES = ("none", "basic", "bearer", "api_key", "oauth2", "hmac")
MAX_RESPONSE_BYTES = 5_000_000
MAX_STORED = 64_000  # of a body, in the log
MAX_SYNC_TIMEOUT = 30  # a synchronous call holds its caller - and, in a flow, its transaction
MAX_ASYNC_TIMEOUT = 120
BREAKER_FAILURES = 5
BREAKER_SECONDS = 60
MAX_BACKOFF_SECONDS = 3600
_SENSITIVE = re.compile(r"authorization|api[-_]?key|secret|token|cookie|signature|password", re.I)
_METADATA = {"169.254.169.254", "fd00:ec2::254", "100.100.100.200"}


class CallFailed(Exception):
    """A call didn't work. retryable: trying again later might (a timeout, a 503); not: it won't (a 400)."""

    def __init__(self, message: str, retryable: bool = False, status: int | None = None, retry_after: int | None = None,
                 response: "HttpResult | None" = None):
        super().__init__(message)
        self.message, self.retryable, self.status, self.retry_after, self.response = message, retryable, status, retry_after, response


class NotConfigured(ValueError):
    """The operation can't be called as set up - a person has to fix that."""


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes
    ms: int


# ---------------------------------------------------------------- the network wall


def _allowed_ranges() -> list[Any]:
    out = []
    for part in get_settings().integration_allow_private_cidrs.split(","):
        if part.strip():
            try:
                out.append(ipaddress.ip_network(part.strip(), strict=False))
            except ValueError:
                log.warning("INTEGRATION_ALLOW_PRIVATE_CIDRS: '%s' is not a network", part)
    return out


def _blocked(ip: str) -> bool:
    address = ipaddress.ip_address(ip.split("%")[0])
    address = getattr(address, "ipv4_mapped", None) or address
    if str(address) in _METADATA:
        return True  # never, whatever is allowed
    if any(address in net for net in _allowed_ranges() if net.version == address.version):
        return False
    return not address.is_global


def check_destination(url: str) -> tuple[Any, str]:
    """(the parsed address, the IP to connect to) - or NotConfigured saying why it may not be called."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise NotConfigured("The address must start with http:// or https:// and name a host")
    if parsed.username or parsed.password:
        raise NotConfigured("Credentials don't go in the address - set them on the connection")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise CallFailed(f"Can't resolve host '{parsed.hostname}'", retryable=True) from exc
    addresses = [info[4][0] for info in infos]
    for ip in addresses:
        if _blocked(ip):
            raise NotConfigured(f"'{parsed.hostname}' is a private or reserved address - the platform administrator must allow "
                                "that network (INTEGRATION_ALLOW_PRIVATE_CIDRS) before it can be called")
    return parsed, addresses[0]


def http_send(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResult:
    """The one network call: to the address just checked, redirects not followed, answer size capped."""
    parsed, ip = check_destination(url)
    host = f"[{ip}]" if ":" in ip else ip
    port = f":{parsed.port}" if parsed.port else ""
    target = urlunsplit((parsed.scheme, f"{host}{port}", parsed.path or "/", parsed.query, ""))
    extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" else {}  # the certificate is checked against the name
    started = time.monotonic()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, target, headers={**headers, "Host": parsed.netloc}, content=body, extensions=extensions) as response:
                data = b""
                for chunk in response.iter_bytes():
                    data += chunk
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise CallFailed(f"The answer is larger than {MAX_RESPONSE_BYTES // 1_000_000} MB")
                return HttpResult(response.status_code, dict(response.headers), data, int((time.monotonic() - started) * 1000))
    except httpx.TimeoutException as exc:
        raise CallFailed(f"No answer within {timeout:g} s", retryable=True) from exc
    except httpx.HTTPError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):  # still retried: certificates do get renewed
            raise CallFailed("The system's TLS certificate isn't valid (expired, self-signed or for another name)", retryable=True) from exc
        raise CallFailed(f"Could not reach the system: {type(exc).__name__}", retryable=True) from exc


# ---------------------------------------------------------------- credentials

_tokens: dict[tuple[int, str], tuple[str, float]] = {}  # per process: (connection, its secret's hash) -> (token, good until)


def _oauth_token(conn: HubConnection, force: bool = False) -> str:
    key = (conn.id, hashlib.sha256((conn.secret or "").encode()).hexdigest())
    held = _tokens.get(key)
    if held and not force and held[1] > time.time() + 30:
        return held[0]
    if not conn.token_url:
        raise NotConfigured(f"{conn.name} uses OAuth2 but has no token address")
    form: dict[str, str] = {"grant_type": "client_credentials"}
    if conn.oauth_scope:
        form["scope"] = conn.oauth_scope
    basic = base64.b64encode(f"{conn.username}:{conn.secret}".encode()).decode()
    result = http_send("POST", conn.token_url, {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded",
                                                "Accept": "application/json"}, urlencode(form).encode(), min(conn.timeout_seconds or 15, MAX_SYNC_TIMEOUT))
    if result.status >= 500:
        raise CallFailed(f"The token service answered {result.status}", retryable=True, status=result.status)
    try:
        answer = json.loads(result.body)
        token = answer["access_token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CallFailed(f"The token service refused ({result.status})", status=result.status) from exc
    _tokens[key] = (token, time.time() + float(answer.get("expires_in") or 300))
    return token


def _auth_headers(conn: HubConnection, body: bytes | None, force_token: bool = False) -> dict[str, str]:
    kind = conn.auth_type or "none"
    if kind == "bearer" and conn.secret:
        return {"Authorization": f"Bearer {conn.secret}"}
    if kind == "api_key" and conn.secret:
        return {conn.auth_header or "X-API-Key": conn.secret}
    if kind == "basic":
        return {"Authorization": "Basic " + base64.b64encode(f"{conn.username}:{conn.secret}".encode()).decode()}
    if kind == "oauth2":
        return {"Authorization": f"Bearer {_oauth_token(conn, force_token)}"}
    if kind == "hmac":  # the receiver recomputes this with the shared secret: it came from us, unchanged, just now
        stamp = str(int(time.time()))
        digest = hmac.new((conn.secret or "").encode(), stamp.encode() + b"." + (body or b""), hashlib.sha256).hexdigest()
        return {"X-BRT-Timestamp": stamp, "X-BRT-Signature": f"sha256={digest}"}
    return {}


def masked_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {k: ("***" if _SENSITIVE.search(k) else str(v)) for k, v in headers.items()}


def masked_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url[:1000]
    query = "&".join(f"{p.split('=', 1)[0]}=***" if _SENSITIVE.search(p.split("=", 1)[0]) else p for p in parts.query.split("&"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))[:1000]


# ---------------------------------------------------------------- building a request


def _json_of(raw: str | None) -> dict[str, Any]:
    try:
        found = json.loads(raw or "{}")
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def reply_url_for(token: str) -> str:
    s = get_settings()
    origin = (s.integration_public_origin or s.training_public_origin or "").rstrip("/")
    return f"{origin}/api/gateway/callback/{token}"


def _soap_envelope(op: HubOperation, inner: str) -> str:
    ns = "http://www.w3.org/2003/05/soap-envelope" if op.soap_version == "1.2" else "http://schemas.xmlsoap.org/soap/envelope/"
    return f'<?xml version="1.0" encoding="UTF-8"?><soap:Envelope xmlns:soap="{ns}"><soap:Body>{inner}</soap:Body></soap:Envelope>'


def build_request(op: HubOperation, conn: HubConnection, payload: Any, correlation_id: str, reply_url: str = "",
                  extra: dict[str, Any] | None = None) -> tuple[str, str, dict[str, str], bytes | None]:
    """(method, url, headers, body) - credentials are added at the last moment and never logged."""
    ctx = {"payload": payload, "correlation_id": correlation_id, "reply_url": reply_url, "now": utcnow().isoformat(timespec="seconds"), **(extra or {})}
    path = mapping.render_path(op.path or "", ctx)
    url = path if path.startswith(("http://", "https://")) else (conn.base_url or "").rstrip("/") + ("/" + path.lstrip("/") if path else "")
    method = (op.method or "POST").upper()
    headers = {"Accept": "application/json, application/xml;q=0.9, */*;q=0.5", "User-Agent": "BRT360-Integration/1.0",
               "X-Correlation-ID": correlation_id, "Idempotency-Key": correlation_id}
    for source in (_json_of(conn.headers_json), _json_of(op.headers_json)):
        headers.update({k: mapping.render_text(str(v), ctx) for k, v in source.items()})
    body: bytes | None = None
    if (op.protocol or "rest") == "soap":
        method = "POST"
        rendered, kind = mapping.render_body(op.body_template, ctx) if (op.body_template or "").strip() else ("", "")
        if kind != "xml":
            rendered = mapping.to_xml(re.sub(r"\W", "_", op.code) + "Request", payload if isinstance(payload, (dict, list)) else {"value": payload})
        document = rendered if "Envelope" in rendered[:300] else _soap_envelope(op, rendered)
        body = document.encode()
        if op.soap_version == "1.2":
            action = f'; action="{escape(op.soap_action)}"' if op.soap_action else ""
            headers["Content-Type"] = f"application/soap+xml; charset=utf-8{action}"
        else:
            headers["Content-Type"], headers["SOAPAction"] = "text/xml; charset=utf-8", f'"{op.soap_action or ""}"'
    elif (op.body_template or "").strip():
        rendered, kind = mapping.render_body(op.body_template, ctx)
        body = rendered.encode()
        headers.setdefault("Content-Type", {"json": "application/json", "xml": "application/xml"}.get(kind, "text/plain; charset=utf-8"))
    elif method in ("GET", "DELETE", "HEAD"):
        if isinstance(payload, dict) and method == "GET":
            scalars = {k: v for k, v in payload.items() if isinstance(v, (str, int, float, bool))}
            if scalars and "{{" not in (op.path or ""):
                url += ("&" if "?" in url else "?") + urlencode(scalars)
    else:
        body = json.dumps(payload if payload is not None else {}, default=str).encode()
        headers.setdefault("Content-Type", "application/json")
    return method, url, headers, body


# ---------------------------------------------------------------- reading the answer


def _fault(root: Any) -> dict[str, str] | None:
    """A SOAP fault in the document, in either version's shape."""
    for element in root.iter():
        if mapping.local(element.tag) == "Fault":
            fault = mapping.to_dict(element) or {}
            if not isinstance(fault, dict):
                return {"code": "", "message": str(fault)}
            code = fault.get("faultcode") or mapping.dig(fault, "Code.Value") or ""
            text = fault.get("faultstring") or mapping.dig(fault, "Reason.Text") or "SOAP fault"
            return {"code": str(code), "message": str(text)}
    return None


def read_answer(result: HttpResult, soap: bool = False) -> tuple[Any, dict[str, str] | None]:
    """(the answer as data, a SOAP fault if it is one)."""
    text = result.body.decode("utf-8", "replace").strip()
    if not text:
        return None, None
    content_type = result.headers.get("content-type", "")
    if soap or "xml" in content_type or text.startswith("<"):
        try:
            root = mapping.parse_xml(result.body)
        except ValueError:
            return text[:MAX_STORED], None
        fault = _fault(root)
        if fault:
            return fault, fault
        body = next((e for e in root.iter() if mapping.local(e.tag) == "Body"), None)
        if body is not None:
            inner = list(body)
            return (mapping.to_dict(inner[0]) if len(inner) == 1 else mapping.to_dict(body)), None
        return mapping.to_dict(root), None
    try:
        return json.loads(text), None
    except ValueError:
        return text[:MAX_STORED], None


def map_reply(op: HubOperation, answer: Any) -> Any:
    rules = mapping.load_mapping(op.reply_mapping_json)
    if not rules:
        return answer
    source = answer if isinstance(answer, dict) else {"value": answer}
    if isinstance(rules, dict):  # {"our name": "path in the reply"} - read in the direction a person thinks of it
        return {k: mapping.dig(source, str(v)) for k, v in rules.items()}
    return mapping.apply(source, rules, keep_unmapped=False)


def _classify(result: HttpResult, op: HubOperation) -> Any:
    """The answer of a call that worked; CallFailed for one that didn't."""
    soap = (op.protocol or "rest") == "soap"
    answer, fault = read_answer(result, soap)
    if fault:
        # the SOAP spec's way of saying whose fault: the sender's won't get better by retrying
        sender = re.search(r"client|sender", fault["code"], re.I) is not None
        raise CallFailed(f"SOAP fault {fault['code']}: {fault['message']}"[:500], retryable=not sender, status=result.status, response=result)
    if 200 <= result.status < 300:
        return answer
    retry_after = None
    if str(result.headers.get("retry-after", "")).isdigit():
        retry_after = int(result.headers["retry-after"])
    said = answer.get("message") or answer.get("error") or answer.get("detail") if isinstance(answer, dict) else None
    message = f"The system answered {result.status}" + (f": {str(said)[:300]}" if said else "")
    if 300 <= result.status < 400:
        message = f"The system redirects ({result.status}) - redirects aren't followed; use the final address"
    raise CallFailed(message, retryable=result.status in (408, 425, 429) or result.status >= 500, status=result.status,
                     retry_after=retry_after, response=result)


# ---------------------------------------------------------------- the circuit breaker


@contextmanager
def independent(db: Session) -> Iterator[Session]:
    """What must outlive the caller's transaction - the log of a call that made its flow fail, the breaker - and
    must not hold a lock on the connection's row for the length of it. SQLite (development, tests) has the one
    connection, so it writes with the caller."""
    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        yield db
        db.flush()
        return
    with Session(bind=getattr(bind, "engine", bind)) as own:
        yield own
        own.commit()


def _breaker_open(conn: HubConnection) -> bool:
    return bool(conn.circuit_open_until and conn.circuit_open_until > utcnow())


def _note_outcome(db: Session, conn: HubConnection, ok: bool) -> None:
    if ok and not conn.failure_count and not conn.circuit_open_until:
        return  # the normal case writes nothing
    conn_id = conn.id
    with independent(db) as session:
        if ok:
            session.execute(update(HubConnection).where(HubConnection.id == conn_id).values(failure_count=0, circuit_open_until=None))
        else:
            session.execute(update(HubConnection).where(HubConnection.id == conn_id).values(failure_count=HubConnection.failure_count + 1))
            count = session.get(HubConnection, conn_id, populate_existing=True).failure_count
            if count >= BREAKER_FAILURES:
                session.execute(update(HubConnection).where(HubConnection.id == conn_id).values(
                    circuit_open_until=utcnow() + dt.timedelta(seconds=BREAKER_SECONDS)))
    if session is db:
        db.expire(conn)


# ---------------------------------------------------------------- one attempt


def attempt(db: Session, op: HubOperation, conn: HubConnection, payload: Any, correlation_id: str, reply_url: str = "",
            timeout: float | None = None, extra: dict[str, Any] | None = None) -> tuple[Any, HttpResult, dict[str, Any]]:
    """One call. (the answer, the raw result, what went out for the log) - or CallFailed, with `sent` on it."""
    method, url, headers, body = build_request(op, conn, payload, correlation_id, reply_url, extra)
    sent = {"method": method, "url": url, "headers": headers, "body": body}
    wait = float(timeout or conn.timeout_seconds or 15)
    try:
        result = http_send(method, url, {**headers, **_auth_headers(conn, body)}, body, wait)
        if result.status == 401 and conn.auth_type == "oauth2":  # the token went stale at their end: one more try with a fresh one
            result = http_send(method, url, {**headers, **_auth_headers(conn, body, force_token=True)}, body, wait)
        answer = _classify(result, op)
    except CallFailed as failure:
        failure.sent = sent  # type: ignore[attr-defined]
        if failure.retryable:
            _note_outcome(db, conn, False)
        raise
    _note_outcome(db, conn, True)
    return answer, result, sent


def _record_exchange(msg: HubMessage, sent: dict[str, Any] | None, result: HttpResult | None) -> None:
    if sent:
        msg.http_method, msg.url = sent["method"], masked_url(sent["url"])
        msg.request_headers = json.dumps(masked_headers(sent["headers"]))
        msg.request_body = (sent["body"] or b"").decode("utf-8", "replace")[:MAX_STORED]
    if result is not None:
        msg.http_status, msg.latency_ms = result.status, result.ms
        msg.response_body = result.body.decode("utf-8", "replace")[:MAX_STORED]


# ---------------------------------------------------------------- calling an operation


def operation_of(db: Session, tenant_id: int, code: str) -> tuple[HubOperation, HubConnection]:
    op = db.query(HubOperation).filter(HubOperation.tenant_id == tenant_id, HubOperation.code == str(code or "").strip().upper()).first()
    if op is None:
        raise NotConfigured(f"There is no integration operation '{code}'")
    if not op.is_active:
        raise NotConfigured(f"Operation {op.code} is switched off")
    conn = db.get(HubConnection, op.connection_id)
    if conn is None or not conn.is_active:
        raise NotConfigured(f"The connection of operation {op.code} is switched off")
    return op, conn


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def _message(tenant_id: int, op: HubOperation, conn: HubConnection, payload: Any, **fields: Any) -> HubMessage:
    return HubMessage(tenant_id=tenant_id, direction="out", kind=fields.pop("kind", "operation"), protocol=op.protocol, pattern=op.pattern, mode=op.mode,
                      connection_id=conn.id, operation_id=op.id, correlation_id=fields.pop("correlation_id", None) or new_correlation_id(),
                      max_attempts=max(1, op.max_attempts or 1), payload=json.dumps(payload, default=str), **fields)


def enqueue(db: Session, tenant_id: int, op: HubOperation, conn: HubConnection, payload: Any, **fields: Any) -> HubMessage:
    """Queued in the caller's transaction: it goes out only if that commits."""
    msg = _message(tenant_id, op, conn, payload, status="queued", next_attempt_at=utcnow(), **fields)
    db.add(msg)
    db.flush()
    return msg


def call(db: Session, tenant_id: int, code: str, payload: Any = None, source: str = "", execution_id: int | None = None,
         force_sync: bool = False, timeout: float | None = None) -> dict[str, Any]:
    """Runs an operation the way it is set up. A synchronous call answers here; an asynchronous one is queued.
    Raises CallFailed when a sync call fails, NotConfigured when it can't be made."""
    op, conn = operation_of(db, tenant_id, code)
    if outbound_guard.simulating():
        return {"simulated": True, "status": "simulated", "operation": op.code, "note": "Nothing was sent - this run is a rehearsal or simulation"}
    if op.mode == "async" and not force_sync:
        msg = enqueue(db, tenant_id, op, conn, payload, source=source[:100], execution_id=execution_id)
        return {"status": "queued", "operation": op.code, "correlation_id": msg.correlation_id, "message_id": msg.id}
    return _call_now(db, tenant_id, op, conn, payload, source, execution_id, timeout)


def _call_now(db: Session, tenant_id: int, op: HubOperation, conn: HubConnection, payload: Any, source: str, execution_id: int | None,
              timeout: float | None) -> dict[str, Any]:
    correlation_id = new_correlation_id()
    protocol, pattern, op_id, conn_id = op.protocol, op.pattern, op.id, conn.id

    def record(status: str, **more: Any) -> None:
        """In a session of its own: a failed call fails the flow, and its log must survive that."""
        with independent(db) as session:
            msg = HubMessage(tenant_id=tenant_id, direction="out", kind="operation", protocol=protocol, pattern=pattern, mode="sync",
                             connection_id=conn_id, operation_id=op_id, correlation_id=correlation_id, source=source[:100],
                             execution_id=execution_id, max_attempts=1, attempts=1, status=status, payload=json.dumps(payload, default=str),
                             finished_at=utcnow(), **{k: v for k, v in more.items() if k not in ("sent", "result")})
            _record_exchange(msg, more.get("sent"), more.get("result"))
            session.add(msg)

    if _breaker_open(conn):
        record("failed", error=f"Not attempted - {conn.name} failed repeatedly and is being left alone until {conn.circuit_open_until:%H:%M:%S} UTC")
        raise CallFailed(f"{conn.name} is failing and is being left alone for a moment - not attempted", retryable=True)
    wait = min(float(timeout or conn.timeout_seconds or 15), MAX_SYNC_TIMEOUT)
    try:
        answer, result, sent = attempt(db, op, conn, payload, correlation_id, timeout=wait)
    except CallFailed as failure:
        record("failed", error=failure.message[:2000], sent=getattr(failure, "sent", None), result=failure.response)
        raise
    except NotConfigured as problem:
        record("failed", error=str(problem)[:2000])
        raise
    reply = map_reply(op, answer) if op.pattern == "request_reply" else None
    record("replied" if op.pattern == "request_reply" else "delivered", sent=sent, result=result,
           reply_json=json.dumps(reply, default=str) if reply is not None else "")
    out = {"status": "replied" if op.pattern == "request_reply" else "delivered", "operation": op.code, "http_status": result.status,
           "correlation_id": correlation_id, "latency_ms": result.ms}
    if op.pattern == "request_reply":
        out["reply"] = reply
    return out


# ---------------------------------------------------------------- sending a queued message (the worker's side)


def next_delay(op: HubOperation | None, attempts: int, retry_after: int | None = None) -> float:
    """Exponential backoff with jitter, so a system coming back up isn't hit by everything at once."""
    base = (op.backoff_seconds if op else 30) or 30
    delay = min(base * (2 ** max(0, attempts - 1)), MAX_BACKOFF_SECONDS)
    delay = delay * random.uniform(0.8, 1.2)
    return max(delay, float(retry_after or 0))


def send_queued(db: Session, msg: HubMessage) -> None:
    """One delivery attempt of a claimed message. Sets its outcome; the caller commits."""
    from app.hub import worker

    op = db.get(HubOperation, msg.operation_id) if msg.operation_id else None
    conn = db.get(HubConnection, msg.connection_id) if msg.connection_id else None
    if op is None or conn is None:
        _finish_failed(db, msg, "dead", "The operation or its connection no longer exists")
        return
    if not op.is_active or not conn.is_active:
        msg.status, msg.next_attempt_at, msg.locked_until = "queued", utcnow() + dt.timedelta(minutes=5), None
        msg.error = f"Waiting - {op.code if not op.is_active else conn.code} is switched off"
        return
    if _breaker_open(conn):  # no attempt used up: it isn't the message's fault
        msg.status, msg.next_attempt_at, msg.locked_until = "queued", conn.circuit_open_until, None
        msg.error = f"Waiting - {conn.name} failed repeatedly and is being left alone for a moment"
        return
    msg.attempts += 1
    reply_url, token = "", None
    wants_callback = op.pattern == "request_reply" and op.reply_via == "callback"
    if wants_callback:
        token = secrets.token_urlsafe(32)
        reply_url = reply_url_for(token)
    try:
        payload = json.loads(msg.payload or "null")
    except ValueError:
        payload = msg.payload
    try:
        answer, result, sent = attempt(db, op, conn, payload, msg.correlation_id, reply_url, timeout=min(conn.timeout_seconds or 15, MAX_ASYNC_TIMEOUT))
    except NotConfigured as problem:
        _finish_failed(db, msg, "dead", str(problem))
        return
    except CallFailed as failure:
        _record_exchange(msg, getattr(failure, "sent", None), failure.response)
        msg.error = failure.message[:2000]
        if failure.retryable and msg.attempts < msg.max_attempts:
            msg.status, msg.locked_until = "queued", None
            msg.next_attempt_at = utcnow() + dt.timedelta(seconds=next_delay(op, msg.attempts, failure.retry_after))
        else:
            _finish_failed(db, msg, "dead", failure.message)
        return
    _record_exchange(msg, sent, result)
    msg.error, msg.locked_until = "", None
    if wants_callback:
        msg.status = "awaiting_reply"
        msg.reply_token_hash = hashlib.sha256(token.encode()).hexdigest()
        msg.reply_deadline = utcnow() + dt.timedelta(minutes=op.reply_timeout_minutes or 1440)
        return
    if op.pattern == "request_reply":
        worker.reply_received(db, msg, op, answer)
    else:
        msg.status, msg.finished_at = "delivered", utcnow()


def _finish_failed(db: Session, msg: HubMessage, status: str, error: str) -> None:
    from app.hub import worker

    msg.status, msg.error, msg.finished_at, msg.locked_until = status, str(error)[:2000], utcnow(), None
    log.warning("Integration message %s (%s) ended %s: %s", msg.id, msg.correlation_id, status, error)
    worker.delivery_failed(db, msg)


def test_call(db: Session, tenant_id: int, op: HubOperation, conn: HubConnection, payload: Any) -> dict[str, Any]:
    """An administrator's Test button: a real call, whatever the operation's mode, with the whole exchange shown."""
    correlation_id = new_correlation_id()
    try:
        answer, result, sent = attempt(db, op, conn, payload, correlation_id, reply_url_for("test-reply-url-not-live"), timeout=min(conn.timeout_seconds or 15, MAX_SYNC_TIMEOUT))
        return {"ok": True, "http_status": result.status, "latency_ms": result.ms, "url": masked_url(sent["url"]), "method": sent["method"],
                "request_headers": masked_headers(sent["headers"]), "request_body": (sent["body"] or b"").decode("utf-8", "replace")[:MAX_STORED],
                "response_body": result.body.decode("utf-8", "replace")[:MAX_STORED], "answer": answer, "reply": map_reply(op, answer)}
    except CallFailed as failure:
        sent = getattr(failure, "sent", None) or {}
        return {"ok": False, "error": failure.message, "retryable": failure.retryable, "http_status": failure.status,
                "url": masked_url(sent.get("url", "")), "method": sent.get("method", ""), "request_headers": masked_headers(sent.get("headers", {})),
                "request_body": (sent.get("body") or b"").decode("utf-8", "replace")[:MAX_STORED],
                "response_body": failure.response.body.decode("utf-8", "replace")[:MAX_STORED] if failure.response else ""}
    except NotConfigured as problem:
        return {"ok": False, "error": str(problem), "retryable": False}


# ---------------------------------------------------------------- Flow Studio's two steps


def flow_call(db: Session, tenant_id: int, execution_id: int, flow_id: int, config: dict[str, Any]) -> dict[str, Any]:
    """The Call external API step: {{steps.<id>.output.reply.*}} in the steps after it."""
    from app.orchestration import flow_errors

    try:
        return call(db, tenant_id, str(config.get("operation") or ""), _flow_payload(config), source=f"flow:{flow_id}", execution_id=execution_id)
    except NotConfigured as problem:
        raise flow_errors.FlowError("NOT_FOUND", str(problem)) from problem
    except CallFailed as failure:
        raise flow_errors.FlowError("INTEGRATION_FAILED", failure.message, "system", retryable=failure.retryable,
                                    details={"http_status": failure.status}) from failure


def flow_wait(db: Session, tenant_id: int, execution_id: int, flow_id: int, run_id: int, config: dict[str, Any]) -> dict[str, Any]:
    """The Call API and wait for reply step: queued with the paused run, so both exist or neither does."""
    from app.orchestration import flow_errors

    try:
        op, conn = operation_of(db, tenant_id, str(config.get("operation") or ""))
    except NotConfigured as problem:
        raise flow_errors.FlowError("NOT_FOUND", str(problem)) from problem
    if op.pattern != "request_reply":
        raise flow_errors.FlowError("INVALID", f"Operation {op.code} is request-only - there is no reply to wait for; use Call external API")
    if outbound_guard.simulating():
        return {"simulated": True, "operation": op.code}
    msg = enqueue(db, tenant_id, op, conn, _flow_payload(config), source=f"flow:{flow_id}", execution_id=execution_id, flow_run_id=run_id)
    msg.mode = "async"
    return {"operation": op.code, "correlation_id": msg.correlation_id, "message_id": msg.id}


def _flow_payload(config: dict[str, Any]) -> Any:
    payload = config.get("payload")
    if isinstance(payload, str) and payload.strip():
        try:
            return json.loads(payload)
        except ValueError:
            return {"value": payload}
    return payload if payload not in (None, "") else {}

"""The Integration Hub (app/hub/): how BRT 360 talks to other systems, both ways.

Inbound - another system calls BRT 360:
- HubClient: a system allowed in. Its own API key (never an administrator's
  sign-in), which services it may call, from where, how often, and which user
  of the tenant its work is done as.
- HubService: something published to those systems - an intent, a flow or a BIM
  interface - callable over REST or SOAP, synchronously or asynchronously.

Outbound - BRT 360 calls another system:
- HubConnection: the system - address, credentials, circuit breaker.
- HubOperation: one call to it - request-reply or request-only, sync or async.
- HubSubscription: "when this event happens, make this call" (a webhook).

HubMessage is every exchange either way: the outbox and retry queue for async
work, the dead-letter queue, and the log. Bodies and secrets are encrypted at rest.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.secret_box import Encrypted
from app.timeutils import utcnow


class HubClient(Base):
    __tablename__ = "hub_clients"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_hub_clients_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(150))
    key_id: Mapped[str] = mapped_column(String(24), unique=True)  # the public part of the key: OAuth client_id, Basic user name
    key_hash: Mapped[str] = mapped_column(String(64))  # the key itself is shown once and never stored
    previous_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # still good while callers move to a rotated key
    previous_key_until: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    run_as_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # whose rights, position and audit trail the work runs under
    allowed_services_json: Mapped[str] = mapped_column(Text, default='["*"]')
    ip_allowlist: Mapped[str] = mapped_column(Text, default="")  # comma-separated addresses / CIDRs; blank = anywhere
    rate_per_minute: Mapped[int] = mapped_column(Integer, default=600)
    expires_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class HubConnection(Base):
    __tablename__ = "hub_connections"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_hub_connections_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(150))
    base_url: Mapped[str] = mapped_column(String(500), default="")
    auth_type: Mapped[str] = mapped_column(String(30), default="none")  # none | basic | bearer | api_key | oauth2 | hmac
    auth_header: Mapped[str] = mapped_column(String(100), default="")  # api_key: the header it goes in
    username: Mapped[str] = mapped_column(String(200), default="")  # basic: user name; oauth2: client_id
    secret: Mapped[str] = mapped_column(Encrypted, default="")
    token_url: Mapped[str] = mapped_column(String(500), default="")  # oauth2
    oauth_scope: Mapped[str] = mapped_column(String(300), default="")
    headers_json: Mapped[str] = mapped_column(Text, default="{}")  # sent on every call
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=15)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # circuit breaker: after repeated failures the system is left alone for a while
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    circuit_open_until: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class HubOperation(Base):
    __tablename__ = "hub_operations"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_hub_operations_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("hub_connections.id"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    protocol: Mapped[str] = mapped_column(String(10), default="rest")  # rest | soap
    method: Mapped[str] = mapped_column(String(10), default="POST")
    path: Mapped[str] = mapped_column(String(500), default="")  # may carry {{placeholders}}
    headers_json: Mapped[str] = mapped_column(Text, default="{}")
    body_template: Mapped[str] = mapped_column(Text, default="")  # JSON or XML with {{placeholders}}; blank: the payload as it is
    soap_action: Mapped[str] = mapped_column(String(300), default="")
    soap_version: Mapped[str] = mapped_column(String(5), default="1.1")
    pattern: Mapped[str] = mapped_column(String(20), default="request_reply")  # request_reply | request_only
    mode: Mapped[str] = mapped_column(String(10), default="sync")  # sync | async
    reply_via: Mapped[str] = mapped_column(String(10), default="response")  # response | callback (the reply comes later, to {{reply_url}})
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    backoff_seconds: Mapped[int] = mapped_column(Integer, default=30)
    reply_timeout_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    reply_mapping_json: Mapped[str] = mapped_column(Text, default="")  # {"<our name>": "<path in the reply>"}; blank: the whole reply
    reply_interface_id: Mapped[int | None] = mapped_column(ForeignKey("bim_interfaces.id"), nullable=True)  # load the reply's records through BIM
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class HubService(Base):
    __tablename__ = "hub_services"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_hub_services_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    target_type: Mapped[str] = mapped_column(String(20), default="action")  # action (an intent or flow) | bim (an interface)
    target: Mapped[str] = mapped_column(String(60))  # INT-15 / FLOW-12 / the interface's code
    mode: Mapped[str] = mapped_column(String(10), default="either")  # sync | async | either (the caller chooses)
    fields_json: Mapped[str] = mapped_column(Text, default="[]")  # the published fields: [{name, type, required, description}]
    input_mapping_json: Mapped[str] = mapped_column(Text, default="")  # the caller's field names -> ours
    output_mapping_json: Mapped[str] = mapped_column(Text, default="")  # the result's fields -> what the caller gets
    callback_operation_id: Mapped[int | None] = mapped_column(ForeignKey("hub_operations.id"), nullable=True)  # told when async work ends
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class HubSubscription(Base):
    __tablename__ = "hub_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    event_pattern: Mapped[str] = mapped_column(String(150))  # "order.*", "flow.executed", "*"
    operation_id: Mapped[int] = mapped_column(ForeignKey("hub_operations.id"))
    start_event_id: Mapped[int] = mapped_column(Integer, default=0)  # nothing from before the subscription existed
    last_event_id: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class HubMessage(Base):
    __tablename__ = "hub_messages"
    __table_args__ = (
        UniqueConstraint("client_id", "service_code", "idempotency_key", name="uq_hub_messages_idempotency"),
        UniqueConstraint("subscription_id", "event_id", name="uq_hub_messages_event"),
        Index("ix_hub_messages_due", "status", "next_attempt_at"),
        Index("ix_hub_messages_tenant", "tenant_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(5))  # in | out
    kind: Mapped[str] = mapped_column(String(20))  # service | operation | webhook | pull | ack
    protocol: Mapped[str] = mapped_column(String(10), default="rest")
    pattern: Mapped[str] = mapped_column(String(20), default="request_reply")
    mode: Mapped[str] = mapped_column(String(10), default="sync")
    # queued -> sending | processing -> delivered | awaiting_reply | replied | succeeded | failed | dead | cancelled | timed_out
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    locked_until: Mapped[dt.datetime | None] = mapped_column(nullable=True)  # a worker that dies mid-send lets go when this passes
    # inbound
    client_id: Mapped[int | None] = mapped_column(ForeignKey("hub_clients.id"), nullable=True)
    service_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # outbound
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("hub_connections.id"), nullable=True)
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("hub_operations.id"), nullable=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("hub_subscriptions.id"), nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reply_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    reply_deadline: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    flow_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the flow waiting for this reply
    # what it was about
    source: Mapped[str] = mapped_column(String(100), default="")  # flow:12 / intent / test / event ...
    execution_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # the exchange
    http_method: Mapped[str] = mapped_column(String(10), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    request_headers: Mapped[str] = mapped_column(Text, default="{}")  # credentials masked
    payload: Mapped[str] = mapped_column(Encrypted, default="")  # what to send / what was received, to retry or replay it
    request_body: Mapped[str] = mapped_column(Encrypted, default="")  # as it went out
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str] = mapped_column(Encrypted, default="")
    reply_json: Mapped[str] = mapped_column(Encrypted, default="")  # the reply, read and mapped
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

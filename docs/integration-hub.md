# Integration Hub

How BRT 360 talks to other systems, both ways. Admin -> **Integration Hub** (and **Integration (BIM)** for bulk data).
Code: `backend/app/hub/`, `experience/routes_gateway.py` (what others call), `experience/routes_hub.py` (administration),
`models/integration_hub.py`. It runs in the `bim` container; its queue runs in the `worker` container.

## Inbound: other systems calling BRT 360

**A client** is a system allowed in. It has its own key, shown once and stored only as a keyed hash - never an
administrator's sign-in. It runs as one chosen user, so that user's position, data scope and intent permissions apply and
the audit trail names them. Optionally limited to certain services, addresses and a rate. A new key leaves the old one
working for 7 days, so a caller can move without an outage.

The key can be presented as `X-API-Key`, `Authorization: Bearer`, HTTP Basic (key id, key), a WS-Security UsernameToken, or
exchanged for a 30-minute token at `POST /api/gateway/oauth/token` (OAuth2 client credentials).

**A service** is what is published: an intent or flow - through the same pipeline as a person, with every check and
approval - or a BIM interface (each active interface publishes itself).

| | |
|---|---|
| `POST /api/gateway/services/{code}` | REST: JSON, XML, or CSV for a BIM interface |
| `POST /api/gateway/soap`, `GET ...?wsdl` | SOAP 1.1 and 1.2, document/literal; WSDL generated |
| `GET /api/gateway/openapi.json` | OpenAPI 3.0, generated |
| `GET /api/gateway/messages/{correlation_id}` | The outcome of a call |
| `GET /api/gateway/ping`, `/services` | Check credentials; what this client may call |

A client's documents list only what it may call.

**Sync or async.** `Prefer: respond-async` (SOAP: `<callMode>async</callMode>`) answers at once - REST 202, SOAP 200 with
`status=accepted`, because SOAP toolkits read a 202 with a body as a fault - and the work goes in the queue. The outcome is
read back, or pushed to the service's callback operation.

**Retries.** `Idempotency-Key` (SOAP: `<idempotencyKey>`) does the work once and returns the first answer to any repeat.

**Dry run.** `?dry_run=true` checks everything and keeps nothing.

## Outbound: BRT 360 calling other systems

**A connection** is the system: address, sign-in (none, basic, bearer, API key, OAuth2 client credentials, HMAC signature),
timeout. Secrets are encrypted at rest and never returned. **An operation** is one call to it: REST or SOAP, a path and body
template with `{{placeholders}}`, and how it talks:

| Pattern | Mode | What happens |
|---|---|---|
| Request-reply | sync | Called during the flow; the answer is there for the next step |
| Request only | sync | Called during the flow; holds it until accepted; no answer kept |
| Request only | async | Queued with the transaction, sent after it, retried |
| Request-reply | async | Queued; the HTTP answer is the reply |
| Request-reply | callback | Queued; the reply comes later to `{{reply_url}}` |

**Prefer async** where the caller doesn't need the answer to go on. A synchronous call in a flow holds that flow's database
transaction for as long as the partner takes (30 s at most).

Used from:
- **Flow Studio** - *Call external API* (`{{steps.<id>.output.reply.*}}`, an `error` output) and *Call API and wait for
  reply*: durable, outputs `replied` / `failed` / `expired`, `{{reply.*}}` afterwards. Rehearsals, simulations and system
  tests never call out.
- **Webhooks** - an event pattern (`order.*`) and an operation; each event goes once, as CloudEvents 1.0.
- **BIM** - a scheduled pull, an acknowledgement after a load, or a reply loaded through an interface.
- **Inbound services** - telling the caller when async work ends.

### What the queue guarantees

- **Transactional outbox**: a queued message is in the same transaction as the business change. A rolled-back order sends
  nothing; a committed one can't lose its message.
- **At-least-once delivery**: a worker that dies mid-send lets go when its lease runs out and the message is retried. Every
  attempt carries the same `Idempotency-Key` and `X-Correlation-ID`, so a receiver can tell a retry from a new request. *The
  receiver has to use it - nothing can make delivery exactly-once from one side.*
- **Backoff**: exponential with jitter, honouring `Retry-After`. Only what might get better is retried: timeouts, 408, 425,
  429, 5xx, a SOAP Server fault. A 4xx or SOAP Client fault goes straight to the dead letters.
- **Circuit breaker**: after 5 failures in a row a connection is left alone for 60 s. Queued messages wait and use up no
  attempts; a sync call fails at once.
- **Dead letters**: kept three times as long as the log, to read and send again from Messages.
- **Order is not guaranteed** between messages. A receiver that needs it must use the event's time or its own version.

### The network wall (SSRF)

An outbound call goes only to http(s), never follows a redirect, and never reaches a private, loopback or link-local
address - so a tenant's administrator can't reach this server's database or containers. The address is resolved, checked and
pinned, so DNS can't change in between; TLS is still verified against the name.

A platform with on-premise systems lists those networks: `INTEGRATION_ALLOW_PRIVATE_CIDRS=10.20.0.0/16,192.168.5.10/32`. **This
is platform-wide**: every tenant can then reach them, so keep it narrow, and never a range holding the Docker network
(172.16.0.0/12 by default). The cloud metadata address is refused whatever is allowed.

## BIM -> EAI

| | Before | Now |
|---|---|---|
| Who calls | An administrator's sign-in | A client's own key, scoped |
| Ids | The source reserves and stores BRT_IDs | Or: a **source key**. BRT 360 issues the BRT_ID on first sight; the source's id updates that record ever after. References by source id: `party_external_id` |
| Mapping | Rename only | Transforms, nested fields, value maps, defaults, fixed values, required; preview; ignore unmapped |
| Protocols | JSON | JSON, XML, CSV, SOAP; generated contracts |
| Sync/async | Sync (big loads lost on a restart) | Either, from a durable queue |
| Getting data | Pushed only | Also a **scheduled pull**, with `{{cursor}}` |
| Result | The response | Also an **acknowledgement** operation |
| Bad rows | Reported | Kept encrypted; download with reasons; reprocess, as they were or corrected |
| Reading back | One record by BRT_ID | Also a **change feed**: `GET /api/gateway/eai/{code}/changes?since=` and `GET /api/gateway/xref/{system}/{entity}/{id}` |

Old interfaces and `POST /api/bim/eai/{code}` work as they did. The change feed shows creations and updates, not deletions.

## The log

Every exchange either way, under Messages: status, attempts, timings, headers with credentials hidden, bodies encrypted at
rest (first 64 KB). Kept `INTEGRATION_LOG_DAYS` days, 30 by default. Message bodies can hold personal data: only tenant
administrators can read them.

## Configuration

| Setting | Meaning |
|---|---|
| `INTEGRATION_PUBLIC_ORIGIN` | The address others reach this platform at, for the WSDL, OpenAPI and `{{reply_url}}`. Blank: `TRAINING_PUBLIC_ORIGIN` |
| `INTEGRATION_ALLOW_PRIVATE_CIDRS` | Private networks outbound calls may reach. Blank: none |
| `INTEGRATION_LOG_DAYS` | How long messages are kept. 0: for ever |

## Not included

- **Mutual TLS** (client certificates), SFTP and message brokers (Kafka, RabbitMQ, JMS, MQ).
- **WS-Security beyond UsernameToken** (no XML signature or encryption), and WS-* generally.
- **The telecom adapters** in `app/integration/` (ERP, provisioning, network activation, billing initiation...) are still
  stand-ins returning made-up references. Reach a real system from a flow or webhook on the same event. Replacing them
  needs a decision per system on what the platform waits for.
- **Calling BIM entities beyond** prospect, lead, opportunity, product: for anything else publish an intent as a service.

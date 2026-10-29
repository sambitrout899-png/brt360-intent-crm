# Module boundaries and the capability registry

Phase 1 of the architecture plan: the application still ships as one
container, but every file now belongs to one of the services it will split
into, and the intent pipeline finds what it can run through a registry
instead of importing each module.

## The modules

`backend/app/boundaries.py` assigns every file under `app/` to a module:

| Module | What it holds |
| --- | --- |
| chassis | settings, database, token checks, tenant scope, BRT_ID, metrics, event outbox, licensing, the capability registry |
| assembly | `main.py`, `seed.py`, `scheduler.py`, `app/models/__init__.py` - the only code allowed to import everything |
| audit, ai, notification | audit log; LLM access and AI settings; notices and outbound notifications |
| identity | users, roles, org structure, record visibility (`access.py`), KYC |
| metadata | Page Designer, custom records and fields, generic record APIs, record lifecycles, approvals |
| product | catalog, Product Modeller, configurator, pricing |
| bim | BIM |
| training | the training module |
| crm | leads, accounts, opportunities, quotes |
| telecom | orders, fulfilment, inventory, field service, assets, tickets, SLAs, communications billing |
| c360, analytics | customer 360; analytics and reports |
| flow | Flow Studio, processes, the function map, test runs |
| intent | classify, reason, plan, run, verify; the agent and the assistant |

Each module lists the modules it `uses`. Any module may use the chassis; nothing
else is allowed without being declared.

## The check

`tests/test_boundaries.py` runs in CI:

- every file must belong to a module;
- an import from one module into another it doesn't declare fails the build,
  unless it is in `tests/boundary_exceptions.txt`;
- an entry in that file that no longer happens also fails, so the list only
  shrinks. After removing a crossing, run `python -m app.boundaries --write`;
- the chassis may not depend on business modules (two known exceptions: the
  event outbox table and the flow table licensing counts).

`python -m app.boundaries` prints the crossings per pair of modules. That list is
the work for each split: a module can move into its own service once nothing
crosses into it except through the registry or its API.

## The capability registry

`backend/app/capabilities.py`. Each module that carries out intents lists them
in its own `BUILDERS = {"INT-xx": build_..._steps}`; the module is named in
`capabilities.PROVIDERS`. The intent API, Flow Studio, the agent and the function
map call `capabilities.step_builder(code)` / `has_steps(code)` /
`intent_codes()`. A code provided by two modules stops the app at start instead
of one silently replacing the other.

Adding an intent: write the step builder in the owning module, add it to that
module's `BUILDERS`; a new actions module also goes into `PROVIDERS`.

When a module becomes its own service, its registry entries turn into calls to
that service; callers don't change.

## Phase 2: the first services in their own containers

`backend/app/service.py` builds every service the same way: CORS, the "who is
acting" stamp, `/api/health` and `/metrics`. The services share the image, the
code, the database (row-level security scopes each request) and token
verification.

| Service | Entry point | What it serves |
| --- | --- | --- |
| training | `app/services/training_app.py` | `/api/training`, `/api/assessments`, `/api/p` (`training.PREFIXES`); the API leaves these out when `SPLIT_SERVICES` names `training` |
| ai-gateway | `app/services/ai_gateway.py` | `/v1/chat/completions` (OpenAI-compatible) for the app's own containers; `llm.chat_json` calls it when `AI_GATEWAY_URL` is set |

In development and in the tests, both settings are empty, so everything still runs
as one app. `tests/test_services_split.py` checks that the training prefixes
match exactly the routes the training service has, that the web container
routes them there, and how the gateway behaves (who may call it, which key,
per-tenant limits, retries, metrics).

**Independent releases.** `training` and `training-worker` (training's
background jobs, `python -m app.scheduler --service training`) run from their
own `brt360-training` image, and the gateway from `brt360-ai-gateway`.
`deploy_docker.ps1 -Service training` rebuilds and replaces only those; the
API keeps running. A release with a database migration is refused as a
single-service release.

**The gateway's guard rails** (`app/services/ai_gateway.py`,
`ai_redact.py`): personal details are masked before a prompt leaves the server
and restored in the answer; there are optional monthly token budgets per tenant;
every call is recorded in `ai_calls` (no prompt text).

**Tamper-evident audit** (`app/crm_core/audit_chain.py`): a per-tenant hash
chain over finished intent runs and training audit entries, append-only on
Postgres, which `/api/admin/audit-chain/verify` checks. It reads those two logs
directly (two recorded crossings) until an event stream feeds it.

**Phase 3: bim, product and metadata in their own containers.** Each split
service is `app/services/<name>.py` (`PREFIXES` and `routers()`), run by
`app/services/split_app.py` with `BRT_SERVICE=<name>`. `main.py` leaves out the
routes of every service named in `SPLIT_SERVICES`. The tests check, per service,
that its prefixes cover exactly its routes, that no route belongs to two services,
and that the web container routes each prefix to the right container. Metadata's
admin settings that live in the shared `routes_admin.py` stay in the API until
that file is divided by module.

Licences stay checked against the tenant row per request rather than carried as
token claims: the row is already loaded for the idle timeout, so claims would save
nothing here, and a licence change would take up to a session's 8 hours to apply.
Claims become worth it when a gateway in front enforces licences without the database.

**Phase 4: durable flow runs on our own engine** (decision 2026-10-29: Postgres,
not Temporal; Temporal can still come later). Flow Studio gains two steps:
**Wait** (minutes, hours or days) and **Wait for approval** (a user or a
position; outputs approved, rejected and expired). A run that reaches one is
saved in `flow_runs` (`app/orchestration/flow_runs.py`), in the same transaction
as everything before it, along with the graph it started on. The worker continues
waits whose time has come and expires approvals; an approver decides in My Work
and the flow continues at once. The rest of a run commits or rolls back as one,
and moves set off their state flows as during an intent. Only flows that run
after something may wait (`flow_engine.can_wait`). Flow Studio lists paused runs
and runs that failed after a wait, and an admin can cancel a waiting run. Steps
after an approval can use `{{approval.decision}}`, `{{approval.note}}` and
`{{approval.by}}`.

**Not split yet: notifications.** `notification_adapter.send` only records a
`notification.sent` event in the outbox; nothing delivers it, because no email
or SMS provider is chosen. The notices bar (`/api/notices`) is a read of
approvals and records owned by other modules. A notification service is
worth its container once there is something to deliver: it would consume
`notification.sent` events from the outbox and send them through the chosen
provider.

**Still shared:** training's code still reaches into three other modules
(c360, intent and flow: five crossings, `python -m app.boundaries`), and every service uses the one database. Giving
training its own schema is the next step for it, once those crossings are
gone.

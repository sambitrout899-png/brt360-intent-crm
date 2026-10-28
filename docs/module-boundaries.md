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

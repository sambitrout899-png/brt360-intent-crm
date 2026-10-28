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

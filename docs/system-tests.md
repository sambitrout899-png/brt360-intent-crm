# System test suite

**Admin -> Test Suite -> System tests.** Test cases generated from what this tenant actually has, run in the background
through the real platform, with step-by-step evidence. Safe on production: every case runs inside a transaction that is
rolled back at the end - nothing a test does is kept (no record, audit entry, notification or number), the same guarantee
as rehearsal.

Code: `app/models/system_tests.py`, `app/orchestration/system_tests.py`, `app/experience/routes_system_tests.py`
(`/api/system-tests`, intent service), `frontend/src/components/SystemTestsPanel.tsx`. Migration `d3f5a7b9c1e4`.

## What is generated

| Source | Cases |
| --- | --- |
| Every intent the admin can run (built-in and Flow Studio custom intents) | **Plain English** - its example wording is understood as that intent; **Required fields** - refused (needs review) without them; **End to end** - run with the values the copilot fills from the tenant's own records |
| Every custom record type | **Lifecycle** - created (valid values for every field, parent and lookups from existing records), changed, moved along its states, deleted; **Mandatory** - each mandatory field and the parent; **Type check**; **Lookup** - a link to a record that doesn't exist; **Validation rule** - each rule made to break (state-dependent rules by moving the record to that state); **Relationship** - linked and unlinked; **Plain English** - "Create a ..." / "Show ..." understood |
| Every switched-on Flow Studio flow | **Flow** - a dry run on the latest record of its type |
| Every page validation rule of the built-in records (customer, lead, opportunity, quote, order) | **Page rule** - values that break it are refused with its message |

Areas follow the Functional Map processes (B2B Sales to Order, Order Fulfilment ...), then "Custom: <type>", "Flows",
"Page rules: <entity>". Cases can be switched off one by one; **Generate again** re-reads the system after changes.

## Running

**▶ Run all in the background** (or chosen areas / kinds). One run at a time per tenant; it continues if the page is
closed and can be cancelled. Each case runs as the admin who started it, with their position's data scope, through
`routes_intent._handle` - the same checks, approvals, flows, cascades and rules people get.

- **passed** - every check held; **failed** - a check didn't (expected vs actual shown); **error** - an unexpected
  exception (with the step); **skipped** - the tenant has no data the case needs (e.g. no customer to hang a record off,
  or the copilot couldn't fill a required field) - add the data, or test it by hand.
- Plain-English cases use the built-in rules by default (fast, no model cost); tick *use our AI model* for exactly what
  people get.

## Evidence

- On screen: per case, each step with its checks (expected / actual), request, response (status, plan, records,
  summary) and the rows it would have written - rolled back.
- **📄 Evidence report** - one self-contained HTML page: summary, results by area, failures first, every step's request,
  response, writes and checks. Open, share, or print to PDF. `GET /api/system-tests/runs/{id}/report.html?download=true`.
- **Test cases (CSV)** - the suite as a manual test script.

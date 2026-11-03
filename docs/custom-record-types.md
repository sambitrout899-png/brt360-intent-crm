# Custom record types (the record type builder)

An administrator's own record types - a Site Survey, a Contract, an Asset Inspection - are first-class records of
BRT 360. They run on the same engines as customer / lead / opportunity / quote / order: the Page Designer, the state
model, Flow Studio, assignment rules, data visibility, the intent pipeline and the audit trail. Licence: **Custom
Development** (`custom_development`).

Code: `app/models/custom_records.py`, `app/crm_core/custom_record_service.py` (records, lookups, relationships,
visibility, validation), `app/crm_core/record_designer.py` (the AI designer), `app/intent_engine/record_intents.py` +
`app/orchestration/actions_records.py` (INT-151..158), `app/experience/routes_custom_records.py`. Migration
`a1c3e5f7b9d2` (after `e2b4d6f8a0c3`). Pages: **Admin -> Record Types** and **Custom Records**.

## Designing a type

**Admin -> Record Types -> ✨ Describe a record type** - in plain words:

> Site Survey for a customer (required): survey date (date, required), floors (number), outcome (Pending, Feasible,
> Not feasible), inspector (lookup to Contact), report (long text). States: scheduled -> in progress -> completed,
> cancelled. Rules: if State is completed then Report is mandatory. Related to Contacts as 'Survey contacts'.

The tenant's AI model drafts the plan (its answer is checked against a closed form, never run); without one the
built-in reader does. Nothing is saved until **Build** - the draft shows the fields, lifecycle, relationships, rules
and notes on anything it left out, and fields can be renamed, made required or dropped first. Naming a type that
exists adds what it lacks. The same is the intent **INT-158 Design a record type** ("Design a record type: ...",
administrators). Or build one by hand: **+ Build one by hand**, then its tabs:

| Tab | What it sets | Engine |
| --- | --- | --- |
| Fields | its own fields - text, number, date, pick list, yes/no, email, phone, long text, **link to a record** (lookup) | custom fields, entity `x_<key>` |
| Lifecycle | states (first = where records start; locked; final) and moves (who may make each) | state model `x_<key>` (also Admin -> Lifecycle API) |
| Relationships | many-to-many links to another type, built-in or custom | `record_relationships` / `record_links` |
| Page & rules | plain-English validation rules; the page itself in the Page Designer | Page Designer, `validation_rules` |
| Automations | the flows on the type; the intents a flow can hang on | Flow Studio, assignment rules |
| Settings | name, plural, icon, number prefix, parent, what its name is called, list columns | |

## Relationships

- **Parent** (one-to-many): a type hangs off a customer, lead, opportunity, quote, order or another of the tenant's
  types (no loops). A record is visible to whoever sees its parent.
- **Lookup field** (many-to-one): a field that points to one record of any type. Given by id, number (`CT-0002`) or
  exact name; the record it points to lists it under **Related**; it can't be deleted while pointed at.
- **Relationship** (many-to-many): defined once (`Site` <-> `Contact`, "Contacts" / "Sites"), records linked from
  either side's Related tab or with INT-155.

`GET /api/custom-records/related/{entity_type}/{id}` lists everything of the tenant's types linked to any record -
a customer's site surveys, an order's inspections.

## Lifecycle, rules and flows

- A new record starts in its model's first state (default model: open -> closed). Moves follow the model; a locked
  state can't be edited; a record approval configured for a state is asked for instead of moving.
- Validation rules run on every save **and before a move** with the new state, so "if State is completed then
  Report is mandatory" stops a survey being completed without its report. A rule can also be a Flow Studio flow.
- Flows: before / after / on-error flows on INT-151..157, state-enter flows on any state, Set Field / Change State /
  Assign / Run an action steps on the record. A flow sees `record.*` (number, name, state, owner, fields),
  `parent.<type>.*` (the records above it) and `customer.*` (the customer up its parents or a customer lookup).
- Assignment: INT-154 or assignment rules (record type picker lists the tenant's types); the owner's position /
  department then sees the record.

## Records and intents

Every write on the Custom Records page runs as an intent through the intent pipeline, so it is audited and its flows
and assignment rules apply:

| Intent | Plain words |
| --- | --- |
| INT-151 Create a record | "Create a site survey for Acme Towers with survey date 2026-11-20 and floors 12" |
| INT-152 Update a record | "Update SS-0004 floors=14", "set the surveyor of SS-0004 to Ravi" |
| INT-153 Move a record | "Move SS-0004 to in progress", "complete SS-0004" |
| INT-154 Assign a record | "Assign SS-0004 to Survey Team" |
| INT-155 Link records | "Link ST-0001 to CT-0002", "unlink CT-0002 from ST-0001" |
| INT-156 Find records | "Show completed site surveys" |
| INT-157 Delete a record | "Delete SS-0007" |
| INT-158 Design a record type | "Design a record type: Asset Inspection for an order with inspected on (date) and passed (yes/no)" |

The tenant's labels, numbers and state names aren't known to static phrase rules, so `classifier.record_step` (db
aware) recognises them before any model runs.

REST (`/api/custom-records/...`): list (`?q=&state=&parent_id=&mine=`), `/{key}/form` (the Page Designer page),
create, `/{key}/{id}` (with state, moves, related records, history), update, `/transition`, `/assign`, `/links`,
delete - each write is the matching intent. Admin: `/api/admin/record-types` (+ `/design`, `/design/build`,
`/relationships`).

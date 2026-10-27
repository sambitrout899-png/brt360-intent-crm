# Modular licensing and Training & Learning

> The Training module was extended to the BRD v1.0 (versions, templates,
> windows, QR, anonymity, analytics, automation) - see
> [training-brd.md](training-brd.md). Everything below still works as written.

## Entitlements

Platform Admin can select modules when creating a tenant and change the same
selection afterward under Edit tenant. An empty selection disables every paid
module without deleting records. Tenant administrators cannot change licenses.

| Key | Module | Scope |
| --- | --- | --- |
| `telecom_crm` | Telecom CRM | Existing customer, sales, product, order, billing and field-service capabilities |
| `training_management` | Training & Learning | Programs, curriculum revisions, sessions, assessments and outcomes |
| `flow_studio` | Flow Studio | Workflow authoring and simulation |
| `page_designer` | Page Designer | Page/form authoring and validation rules |
| `custom_development` | Custom Development | Custom fields, intents, function maps and process/scratch builders |

Authentication, organization structure, user management, audit and shared runtime
services remain platform capabilities. Authoring licenses are independent:
functional modules can execute previously configured forms and workflow hooks
without requiring an authoring seat. Custom intent execution requires the custom
development entitlement; embedded Telecom actions still require Telecom CRM.

Existing tenants with NULL entitlements retain their former Telecom and builder
access. New tenants default to Telecom CRM only, unless another selection is
made. Telecom reference data is seeded when Telecom is first activated. Licenses
are organization-level feature entitlements; pricing, invoicing, seat quotas and
commercial expiration dates are not implemented.

Server checks protect direct module APIs, intent availability, shared lookup/form
APIs and flow actions. Navigation and admin sections reflect licenses, intersected
with existing user page access. Reload an open tenant session after changing its
licenses; server-side revocation takes effect on the next request.

## Training workflow

1. Create the organization with Training & Learning selected.
2. Create users and departments in Admin. Assign training roles and locations in
   Training & Learning > People. Tenant admins can assign roles; HR manages
   programs and sessions; facilitators manage their assigned sessions; executives
   read outcomes; participants self-register and complete assessments.
3. Create a program with curriculum modules and scored multiple-choice questions.
   Questions carry a learning outcome, which groups outcome reporting. Publishing
   a new revision never changes the curriculum/questions of an existing session.
4. Schedule a version with facilitator, location, department, capacity and dates.
   Overlapping facilitator schedules are rejected. Record facilitator
   certifications and QA notes in Certifications.
5. Register participants and record attendance. Pre-assessments are issued at
   registration and expire when the session starts. Post-assessments are issued
   after the session ends for participants marked present and expire seven days
   after its end. Cancelled sessions/registrations invalidate links.
6. Participants open their personal in-app invitations or scan their QR code.
   Assessments work without signing in, using a cryptographically random,
   single-use token. They may respond anonymously or identify their answers for
   matched before/after comparisons. Reports separate phase averages from matched
   participant changes and group knowledge scores by learning outcome.

Anonymous response rows omit user identity and actor/timestamp metadata; no
response-to-invitation FK is retained. This is application-level anonymity, not
protection against database administrators correlating infrastructure logs or
small cohorts. Anonymous answers cannot be individually matched across phases.

The worker checks invitations/reminders once per minute. A single reminder is
created in the final 24 hours before expiry. Deliveries are idempotent and appear
in the participant's in-app inbox. **External email/SMS delivery is not wired**;
the application's existing notification adapter is a stub. The private delivery
outbox holds assessment URLs; do not expose it through generic reporting.

## Shared primitives and functional intents

Training uses the existing tenant/user/department infrastructure, custom field
storage, Page Designer validation, Flow Studio runtime and intent audit log.
Training program details can be designed in Page Designer, including required
custom fields and validation rules. The program creation screen renders that
shared form.

`POST /api/training/intents` accepts `{ "code": "TRN-01", "entities": {...} }`:

| Intent | Operation |
| --- | --- |
| TRN-01 | Create program and initial curriculum revision |
| TRN-02 | Publish a new curriculum revision |
| TRN-03 | Schedule session |
| TRN-04 | Register participant |
| TRN-05 | Record attendance |
| TRN-06 | Process due assessment invitations/reminders |

The same operations are exposed through typed training REST endpoints and the
training UI. Both paths apply the same role and tenant checks. Functional actions
and before/after Flow Studio hooks execute in one database transaction and write
the shared intent audit log. Training flow triggers are available in Flow Studio;
use `entities.*` inputs and `result.*` outputs, without a CRM record target.
Training intents use structured forms; they are not yet added to the general
natural-language Telecom command-bar classifier. Training-specific lifecycle
states, generic CRM record mutation nodes and arbitrary custom entities are not
part of this implementation.

## Installation and validation

From `backend`, install requirements and run `alembic upgrade head`. The additive
migration `a9c2e5f8b1d4` introduces tenant entitlements and training tables. Back up
an existing database before applying the full pending migration chain.

Set `TRAINING_PUBLIC_ORIGIN` to the public frontend origin, for example
`https://learning.example.com`. Assessment QR codes encode this configured origin
rather than trusting the request Host header. The web server must serve the SPA
for `/assessment/*`. `qrcode[pil]` is included in backend requirements. No external
QR service receives assessment tokens.

Build the frontend with `npm run build`. Run `pytest tests/test_modular_training.py`
for license changes, access boundaries, assessments, anonymity, workflow rollback,
page validation, reminders and QR generation. `tests/test_migrated_schema.py`
validates the real migration chain on an isolated temporary database.

## Module-specific authoring catalogs

Intent administration, functional maps (including vanilla/restore), Page Designer
forms/rules, and Flow Studio lists and triggers are filtered by the selected
functional modules. Custom workflows that target or invoke an unselected module
are hidden too. Direct edit requests are checked server-side. Filtered map saves
retain hidden modules' settings, layout positions and connections so reactivation
restores them. Shared authoring tools remain available when separately licensed.

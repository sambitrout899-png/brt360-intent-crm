# Training Module - BRD v1.0 build

How the BRT 360 Training & Learning module meets the BRD (15 Oct 2026), where
each requirement lives, and what is not built yet. Decisions are logged in
[`DECISIONS.md`](../DECISIONS.md). The original module (programs, revisions,
single-form assessments) is described in
[`modular-licensing-training.md`](modular-licensing-training.md) and keeps
working unchanged.

## Where to find it

| | |
| --- | --- |
| Staff UI | Left menu → **Learning**: Learning home, My trainings, Sessions, Programs, Assessments, People & places, Facilitators, Outcomes, Messages, Learning settings, Learning audit log. Each is a page key (`training_*`) under **Admin → Roles**, so roles get exactly the pages they need; the page's APIs follow the grant (`routes_training_brd.PAGE_RULES`). A role that ticks only **Learning home** (every role saved before the split) keeps all Learning pages. Inside a page, what a person can change still follows their training role. The original screens are under Learning home → **Classic view**. |
| Participant UI (no login) | `/p/a/<token>` assessment · `/p/q/<code>` session QR (check-in, my assessment, anonymous feedback) · `/p/r/<code>` self-registration · `/p/u/<token>` stop reminders |
| Staff API | `/api/training/...` (see `/docs`, tag `training`) |
| Public API | `/api/p/...` (tag `training-public`, rate limited) |
| Services | `backend/app/crm_core/training_policy.py` (settings, permission matrix, scoping, audit), `training_catalog.py` (locations, people, programs, versions, templates, certifications), `training_delivery.py` (sessions, lifecycle, registration, attendance, QR), `training_assess.py` (links, player, scoring, anonymity), `training_analytics.py` (outcomes, dashboards, compare, scorecards, QA), `training_notify.py` (messages, scheduler), `training_scoring.py` (pure 10.3 formulas), `training_export.py` (CSV / XLSX / PDF) |
| Migration | `backend/migrations/versions/b2d4f6a8c0e3_training_brd.py` (additive) |
| Tests | `tests/test_training_brd.py` (requirement IDs in every test name), `tests/test_training_scoring.py` (10.3 table tests), `tests/test_modular_training.py` (original module, unchanged) |
| Demo data | `python -m scripts.seed_training_demo --tenant-slug default` (needs the Training licence; does nothing once seeded) |

Configuration: `TRAINING_PUBLIC_ORIGIN` (links and QR codes), `TRAINING_EMAIL_ADAPTER` (`outbox` default or `smtp`) and `TRAINING_SMTP_*` - see `backend/.env.example`.

## Typical flow

1. **People & places**: add locations; give users their training roles (several per person) and, for HR/Executive, a location/department scope; import participants without logins from CSV.
2. **Assessments**: build a pre and a post template sharing at least one Knowledge and one Confidence `item_key`, each mapped to an objective code; optionally a feedback form.
3. **Programs**: create the program (draft v1.0), add modules (drag to reorder) and objectives, link the templates, publish. Editing later means **New minor/major version**; versions can be compared and retired.
4. **Sessions**: schedule from a published version (certified, un-double-booked facilitators), register people (nominate, bulk CSV, self-registration, public link), let the waitlist fill.
5. The scheduler sends the confirmation and pre link, the reminders (only to those who haven't done it), the session-day note with the check-in QR, then after the session the post + feedback links to those marked present or late.
6. **Outcomes** appear as soon as post assessments come in: per session (Sessions → session → Results) and across sessions (Outcomes), exportable to CSV / XLSX / PDF.

## Requirement status

✅ built and tested · 🟡 partly built (see note) · ⬜ not built

### Organization and users
| ID | | Where / note |
| --- | --- | --- |
| ORG-001 | ⬜ | Tenant suspend/reactivate is a platform change (D-20). Tenants and their settings already exist. |
| ORG-002 | ✅ | Locations; sessions only at active ones. `catalog.save_location`, `delivery.create_session` |
| ORG-003 | 🟡 | Uses the platform's department tree (parent/child); scoping includes sub-departments. Ancestor-cycle and archive checks belong to platform Admin → Organization. |
| ORG-004 | ✅ | Single add, CSV import validating every row (dry run + import valid rows), duplicate email rejected. Login accounts are still created under Admin → Users. |
| ORG-005 | ✅ | Six roles, several per user (union), one policy table `training_policy.POLICY`, 403 per role tested. |
| ORG-006 | ✅ | HR/Executive scoped to locations/departments (with children); out-of-scope sessions are absent, not errors. |
| ORG-007 | 🟡 | Passwords are bcrypt cost 12 (platform). MFA, 5-attempt lockout and idle timeout are not built (D-20). |
| ORG-008 | ⬜ | SSO (release 2). |
| ORG-009 | ✅ | Platform users belong to one tenant; every training path is tenant-filtered (cross-tenant tests). |
| ORG-010 | ✅ | Settings (anonymity default, reminders, pass mark, retention, windows, rubric...), validated and versioned in the audit log. Branding uses the platform's tenant branding. |
| ORG-011 | ✅ | Erasure replaces personal fields with a token; aggregates stay. (Platform users are deactivated under Admin.) |
| ORG-012 | ✅ | Append-only training audit log (actor, action, entity, before/after, time, IP) with CSV export. |

### Programs, sessions, facilitators
| ID | | Where / note |
| --- | --- | --- |
| PRG-001..004, 006 | ✅ | Programs with code/category/audience/mode/duration/objectives; ordered modules with materials and notes; draft → published → retired, immutable once published; pre/post required unless waived with a reason; required certification. |
| PRG-005 | ✅ | Version diff: added / removed / changed modules with field names, plus objective changes. |
| SES-001, 003..010 | ✅ | Schedule from published versions only; lead + co-facilitators with BR-FAC-01 (admin override with reason, audited) and BR-SES-02 (30-min travel buffer); state machine with audit; nomination, bulk CSV, self and public registration; capacity, deadline, waitlist with auto-promotion; external participants without passwords; attendance by roster, QR (window -30/+60 min) and upload, each with method, time, recorder; month/week/list calendar with filters and venue time zone. |
| SES-002 | ⬜ | Multi-day sittings (release 2). |
| SES-011 | ⬜ | .ics attachments (release 2). |
| SES-012 | 🟡 | Session notes; file uploads with virus scanning are not built (Could). |
| FAC-001..004 | ✅ | Profiles (bio, expertise, languages, external, vendor), certificates with computed Valid/Expiring/Expired/Revoked, QA reviews on an org rubric, scorecards from the analytics engine. |
| FAC-005 | ✅ | Expiry alerts at -60/-14 days and rating-below-threshold alerts, once per crossing. |

### Assessments
| ID | | Where / note |
| --- | --- | --- |
| ASM-001..005 | ✅ | Versioned templates (clone when in use), 8 question types, measures, points, item_key pairing and objective mapping enforced at publish. |
| ASM-006 | ⬜ | Question bank (release 2). |
| ASM-007 | 🟡 | Randomized questions/options and one attempt work; the time limit is stored but not enforced by the player. |
| ASM-010 | 🟡 | One question per screen under 640 px, progress bar, 44 px targets. Lighthouse and 4G timing not measured yet. |
| ASM-011..016 | ✅ | Unguessable hashed links, windows (pre until start + 15 min, post 7 days), session QR with OTP or phone check, anonymous QR with one-per-device cookie, autosave and resume, final submission with own scores when enabled. |
| ASM-017 | 🟡 | Answers are kept on the device and survive a dropped connection; no offline service worker (Could). |
| ASM-020..023 | ✅ | Identifiable or anonymous per session (locked after the first response), unlinkable anonymous table, completion flag only, results hidden below the minimum group. |
| ASM-024 | ⬜ | Redaction of anonymous comments (release 2). |

### Analytics and automation
| ID | | Where / note |
| --- | --- | --- |
| ANL-001..005, 008 | ✅ | Recomputed on every post submission and at close; normalized gain (excluded at 100%); per objective with "not measured"; funnel, bands, feedback, participant drill-down; comparison by session/facilitator/location/department/version/program with dates; CSV/XLSX/PDF exports within scope. |
| ANL-006, 007 | ✅ | Wilcoxon (n < 30) or paired t-test, p-value and Cohen's d, suppressed under 5; knowledge-gap and confidence-competence flags. |
| WFA-001..003, 005..010 | ✅ | Planned per session and re-planned on reschedule; completion checked at send time; templates with checked merge fields and preview; quiet hours 21:00-08:00 local; delivery statuses incl. hard bounce → invalid email + HR alert; retries 1/5/30 min with idempotency keys; resend reusing or regenerating the link; unsubscribe from reminders; per-session log. |
| WFA-004 | 🟡 | Email via outbox or SMTP adapter; SMS/WhatsApp have the adapter slot but no provider. |

### Business rules
BR-ORG-01, BR-FAC-01, BR-SES-02, BR-SES-03, BR-SES-04, BR-ASM-01..04 and BR-WFA-01 are enforced in the services and each has a test named after it.

### Non-functional
| ID | | Note |
| --- | --- | --- |
| NFR-001 | 🟡 | Service-layer isolation with a cross-tenant test suite; PostgreSQL RLS waits for a PostgreSQL deployment (D-03). |
| NFR-003 | 🟡 | Parameterized queries, input validation, React escaping, CSV formula neutralising, 30 req/min/IP on public links (per worker, D-07). Pen test not done. |
| NFR-004 | ✅ | Consent on first assessment and on public registration; erasure; retention purge with audit. Data export per person is not built. |
| NFR-005 | ✅ | Schema test proves no path from an anonymous response to a person. |
| NFR-011 | ✅ | Audit entries for configuration, people, sessions, templates, results and exports. |
| NFR-002, 006..010, 012..015 | ⬜ | Infrastructure, load, accessibility automation, observability, localization and residency work is outside this build. |

## Not yet built - suggested next steps
1. Platform security: MFA (TOTP), lockout after 5 failures, idle timeout, organization suspend/reactivate (ORG-001, ORG-007) - affects every module.
2. .ics invites and multi-day sittings (SES-011, SES-002).
3. Question bank, time-limit enforcement, comment redaction (ASM-006/007/024).
4. SMS / WhatsApp provider adapter (WFA-004) once a provider is chosen.
5. PostgreSQL with RLS policies, and a shared rate limiter in nginx.

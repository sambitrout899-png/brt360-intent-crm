# Customer 360 account summary

The default customer page uses a compact account workspace inspired by the account-summary pattern: a single identity header, account figures, installed assets on the left, and recent orders and service requests on the right.

The header shows the account number, BRT ID, segment, identity status and contact information once. Account figures appear only on the summary. The customer selector no longer repeats revenue and deal values beside every customer.

Each record has a dedicated view:

- Installed assets: search, status filters, read-only configuration, pricing, linked order history and asset-based change-order actions. A product link in the summary opens the selected asset here.
- Orders: one list across service accounts, with search and order links. Recent orders in the summary are a preview of this list.
- Service requests: one request list, expandable request details and the existing new-request and diagnosis actions.
- Sales, Billing, Profile, Hierarchy & preferences and Activity retain their existing workflows.

The two branches are Account/prospect → Billing account → Bill profiles → Bills/invoices and linked payments, and Account/prospect → Service accounts → Installed assets. Service accounts own their assets directly; their billing-account reference identifies the payer. Assets are grouped beneath each service account. Billing profiles live only in the Billing view, with profile selection filtering the invoices and linked payments. Account-level totals and records without profile references are explicitly labelled.

The former Live services list, large journey banner, repeated KPI widgets and overlapping service-account/order sections are removed from the main summary. Identity history and the customer journey are available on demand under Profile.

Migration `e8b0d2f4a6c9` adds the explicit ownership links. Deployment seed reconciles exact legacy `Service` placeholders from work orders that lacked a line reference. A placeholder is superseded only when its order has exactly one product line and one canonical asset, with matching tenant and service account and no independent revision/change history. It remains in the database for audit. Different lines, orders and equipment are never merged on product name alone.

Configure 360 is the source of truth for theme, accent, card treatment, density, header style, headline metrics, health and published panes. Published panes appear in their original order in their domain's tab, using the same renderer and domain mapping as the studio preview. All published widgets are retained. A configured list replaces its matching built-in fallback; charts and metrics do not remove operational lists. Saved pane widths and collapsed states are respected. Service hierarchy/asset operations and the billing-profile workspace remain accessible on demand when custom lists occupy those tabs. An optional connected-data failure does not stop the core account summary loading.

Visual review used synthetic data at desktop, laptop and phone widths. Browser checks covered duplicate suppression, asset selection and change actions, order search and links, service request details and diagnosis, empty accounts, connector failure and horizontal overflow. The screenshot files in `docs/deliverables/account-summary-*.png` show the synthetic preview, not production customer data.

Configure 360 regression checks are in `frontend/scripts/c360-presentation.test.mjs`. Additional browser verification exercised the actual studio's Look → Publish → Close flow with synthetic data, confirmed that the published version refreshed immediately, and checked Aurora/Daylight themes, card styles, density, header style, KPI/health visibility, domain panes, published widths and collapsed state. The `configure360-*-published.png` screenshots show those sample layouts. The workspace theme uses the published colour tokens throughout its header, selector, tables, cards and operational panels.

Configure 360 supports customer audiences on every pane and widget. In the pane or widget inspector, choose Accounts/Prospects and B2B/B2C. Both dimensions must match; a widget must also match its parent pane. Create separate panes for different combinations to vary content, order, width, title, data source and charts. The Design canvas keeps every pane editable and labels its audience. Preview uses a real customer's profile and displays the resolved type and segment.

The Page inspector's Standard component audience selector configures account information, service accounts/assets, orders, sales, billing, requests, alerts, health, profile, hierarchy and activity, plus each selected headline metric. These rules control native sections independently of added panes. For example, target standard installed assets to Accounts and add a Prospect/B2C pane for acquisition content. Publish once; customer selection and profile changes automatically select the matching content on reload. Audience rules are presentation settings; existing authorization still applies.

Old layouts default to all customers. All segments includes unclassified customers; B2B-only and B2C-only exclude them. Empty selections or disabling a component hide it. Audience rules are versioned and preserved by publish, undo and restore. Excluded panes/widgets are filtered on the server before their data is loaded. Configured lists reserve their native replacement even when excluded, preventing an unwanted fallback on the same tab. Preview lists which standard sections are shown or hidden; its canvas renders configured panes.

Audience regression tests cover all four type/segment combinations, parent/child rule intersection, same-customer reclassification, unknown segments, legacy layouts, no data fetch for excluded widgets, publish/undo/restore and AI operation preservation. Browser verification with synthetic customers covers the actual audience controls, publish persistence and switching between a B2C Prospect and a B2B Account. Screenshots: `configure360-audience-design.png` and `configure360-audience-preview.png` in `docs/deliverables`.

Deployment uses the existing full-workspace command:

```powershell
cd "D:\BRT 360\brt360-intent-crm"
powershell -ExecutionPolicy Bypass -File ".\deploy\deploy_fast.ps1" -Typecheck
```

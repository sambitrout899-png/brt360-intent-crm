# Asset-based ordering

Fulfillment completes the order and every order line. Each new-connect line creates one installed asset under the service account already on the order. Its read-only instance contains the frozen product definition, configuration, technical values, pricing, quantity, model version and source instance. Repeated completion does not duplicate assets.

Customer 360 → service accounts displays installed assets, their instances, versions and originating/change orders. Revenue and live-service counts use installed assets; change orders are not counted as additional subscriptions.

Use **INT-107 Change an asset** from an asset. Modify, disconnect, terminate, suspend, resume, relocation, customer transfer and number portability create a distinct order. The request carries the asset ID and base version. Only one open (submitted) change per asset is permitted; a change with a later **due date** queues behind it (see below). Until fulfillment, the installed asset is unchanged. Completion updates the same asset and appends a revision; the original completed order/line/instance cannot be edited.

Relocation requires a destination site and another service account for the same customer. Customer transfer requires the receiving customer's service account in the same tenant and validates product eligibility. Portability requires the number and donor operator. Suspend/resume/termination change the asset's lifecycle, not the historical new-connect order.

**INT-108 Confirm order fulfillment** is an administrator confirmation entry point for orders submitted to orchestration. It must only be invoked after actual fulfillment. The existing orchestration adapter is a deterministic stand-in, not a connection to a live carrier, porting authority or network. Submission alone does not claim those external operations succeeded. Production integrations must deliver their actual completion through the completion service. Existing immediate activation and acceptance-letter fulfillment paths call this same service.

Modification validates against the frozen product definition and price rules. Discounts that require approval are blocked rather than silently bypassing approval. This release does not implement a new approval workflow specifically for asset changes.

Deployment includes migration `e5a7c9d1f3b6` and seed-time adoption of existing new-connect orders in active/changed/suspended states. Existing product instances are copied. For older records without a saved product definition, the available catalog definition is captured with the stored line configuration; the historical definition cannot be reconstructed. Existing new-connect orders become Completed, and suspended assets remain suspended. The script backs up the database before migration.

## Siebel asset-based ordering - the complete set

| Siebel ABO | BRT 360 |
|---|---|
| Delta quote / order with action codes | Every change order has its lines: the service's line **Update / Suspend / Resume / Delete**, and a line per bundle component **Add / Delete / Update / -** (priced in the service's line). The note shows each attribute's old and new value. A product change or a relocation is a **Delete** of the old line and an **Add** of the new one. The INT-107 result carries the same `delta`. |
| Auto-Asset | Fulfillment (INT-108) marks the change order's lines complete. It updates the asset as a new revision, or for a product change or relocation it closes the old asset and installs a new one. |
| Open orders and the requested asset | A change dated after an open change order is **queued** behind it. It is built from what that order makes (the future, requested asset) and is fulfilled after it: INT-108 refuses it until then, and it becomes the open change once the earlier one completes. Nothing can be queued after a disconnect, a product change or a relocation. |
| Cancel an order | **INT-110 Cancel an asset change** (or INT-61 on a change order) stops it and leaves the asset as it is. A change with another queued behind it is cancelled after that one. |
| Revise (supplemental order) | **INT-111 Revise an asset change** works out a new revision from what was asked plus what changes now. It replaces the in-flight change order (the old one is closed as *revised*). The point of no return applies. |
| Modify → Quote → Order | **INT-114 Quote an asset change** prices the change, with its delta lines and any penalty, as change quote `AQ-n`, valid 30 days. **INT-115 Order a change quote** orders it, provided the quote is still valid and the service hasn't changed since it was quoted. |
| Bulk request | **INT-112 Bulk asset change** applies one change to the listed assets, or to every live service of a customer (optionally of one product). Each asset gets its own change order. An asset that can't change is reported and the rest go ahead. |
| Asset history / point-in-time | **INT-113 Asset history** shows the revisions, every change order and its status (submitted, queued, completed, cancelled or revised), the chain of assets a service has been, what it was **as of** a date, and the requested state once its open orders are fulfilled. |

Migration `b3d5f7a9c1e2` adds the due date, queue, revision, reason and completion columns to `asset_changes`, plus the `asset_quotes` table. It only adds columns, so the "one open change per asset" index is untouched.

```powershell
powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\package_full.ps1" -SkipChecks
powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_sto.ps1" -SkipTests
```

`deploy_fast.ps1` is not safe for this release; use the two commands above. No production deployment was performed while implementing this change.

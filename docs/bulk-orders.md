# Bulk orders

Many orders in one request - each row runs the **same single-transaction process** as one order, intent by intent,
and is followed until **fulfilment completes, the services are installed as assets and billing has started**.

Code: `app/models/bulk_orders.py`, `app/orchestration/bulk_orders.py`, `app/intent_engine/bulk_intents.py` (INT-149 Start a
bulk order, INT-150 Bulk order status), `app/experience/routes_bulk_orders.py` (`/api/bulk-orders`, intent service), page
**Operations -> Bulk Orders** (page key `bulk_orders`, Telecom CRM licence). Migration `f9b1d3e5a7c9`.

## Processes

| Process | Each row runs | Required columns |
| --- | --- | --- |
| B2C new connect | INT-41 Add a consumer (new customer) -> INT-104 Place a B2C order (feasibility, payment, submit) -> INT-25 Approve the order -> INT-117 Send to Fulfillment (if no pattern started) | `products` + `party_id` or `customer_name` |
| B2B quote to order | INT-24 Create the order -> INT-25 -> INT-117 | `quote_id` (approved, accepted) |
| Mobile lines | INT-41 / INT-15 (new customer) -> INT-136 Activate mobile lines | `offering_name` + `party_id` or `customer_name` |
| Asset changes | INT-107 Change an asset -> INT-117 (a change pattern) or INT-108 Confirm order fulfillment | `asset_id`, `asset_operation` |

Download each process's CSV template on the page (or `GET /api/bulk-orders/processes`). "Every row gets" sets values all
rows share (e.g. `order_channel=store; products=Home Fiber 100`); attribute columns take `name=value; ...`.

## How rows run

- Every step is an intent through the intent pipeline **as the person who submitted the request** (their position and data
  scope): catalog availability, role checks, the approval matrix, Flow Studio before / after flows, fulfilment triggers
  and the audit trail - exactly as for a single order. Each step is its own audit entry (`BULK-000012 row 3: INT-104`).
- Rows are validated when the request is created (invalid rows never run). A row that can't go on stops with the
  intent's own reason; the others go ahead. **Retry failed** queues rows that never made an order again.
- Stages: queued -> ordering -> (discount approval / order approval / ready for approval) -> in fulfilment (fallout) ->
  completed. A row waiting for an engineer's work order, an approver or an inventory answer simply waits; a fallout is
  resolved in Fulfillment (INT-118) and the row carries on.
- *Approve each order along the way* (on by default) runs Order Control approval (INT-25) for each booked order - off,
  each waits for its approver. *Send orders to Fulfillment* hands an order no pattern started to one that fits.
- The scheduler moves running requests along every minute (25 new rows per request per pass); **Process now** does a
  pass at once. **Results CSV** gives every row with its orders, assets, billing and stage.
- Up to 5,000 rows per request.

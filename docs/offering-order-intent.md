# INT-106: Order an offering

Built-in intent; no quote or AI retraining required. Inputs are `party_id` (record number or quoted BRT ID), `offering_name` (exact active catalog name), `quantity` (positive integer, default 1), and `order_mode` (`interactive`, the default, or `silent`).

In Flow Studio choose Run an action → INT-106. Map the customer and offering from the triggering intent or a previous node using the expression builder. For example:

```json
{
  "party_id": "{{flow.inputs.parameters.party_id}}",
  "offering_name": "{{flow.inputs.parameters.offering_name}}",
  "order_mode": "silent",
  "quantity": 1
}
```

Silent mode uses the customer's default price list, product configuration defaults and zero discount. It ignores supplied pricing/technical overrides and discounts. Missing required technical defaults cause an error before creating the order. Eligibility, catalog floor/discount limits and existing INT-07 provisioning behavior apply.

Interactive mode returns a Configure order button without creating an order. The form exposes the product pricing configurator, technical attributes and order line discount. Submit for provisioning explicitly sends `submit_for_provisioning: true`. Pricing is calculated from the selected product attributes, not an unrestricted monetary override. Approval rules can hold provisioning for a discounted order.

When a flow invokes interactive mode, that flow invocation ends at the handoff. It does not automatically resume downstream nodes. Put operations requiring the submitted order in an after-flow on INT-106; those after-flows run only on submission, not on the initial configuration request. Silent mode continues to later nodes normally.

Submission returns `order_id`, `order_item_id`, `product_offering_id`, `party_id`, `offering_name`, `quantity`, `unit_price`, `discount_pct`, `net_unit_price` and provisioning/approval results. The order line stores its prices and discount and displays them under Orders → Configuration.

Deploy backend, frontend and the additive order-line price migration together using the existing single deployment script:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_fast.ps1" -Typecheck
```

This command deploys the full current workspace, including other local changes. Add `-DryRun` to build/package without connecting to the server. Deployment has not been run as part of this implementation.

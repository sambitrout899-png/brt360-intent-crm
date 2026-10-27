# Record operations in Flow Studio

## Expression builder and triggering intents

The flow definition displays **Triggering intent → Flow input parameters**, and every node displays the same available inputs. Use the first source group, **Flow inputs · from triggering intent**, to map those values. `flow.inputs.parameters` combines the original intent inputs and outputs (outputs win for the same key). `flow.inputs.records.customer`, `product`, `order`, etc. contain tenant-scoped record snapshots when the intent identifies those records. For example, customer name and record number are `flow.inputs.records.customer.name` and `.id`. Products resolved by name must be unambiguous; a list of eligible products does not imply a selected product. The table defines available paths, not live customer values; values are filled on execution.

Mapping fields now open in **Expression builder** mode. Add a destination field, select **Use a field / previous output**, search, and choose from **Trigger outputs**, **Trigger inputs**, **Previous steps**, or **Current record**. Constants support text, numbers, booleans and null. Existing JSON mappings remain compatible; use **Advanced JSON** for compound objects and mixed text expressions.

An after-intent flow automatically receives the vanilla intent's complete original output in `trigger.output` and its inputs in `trigger.input`. These remain separate from later action outputs. The legacy `entities` and `result` references remain supported. No manual copying is needed during real execution. Before-intent flows do not yet have the intent's output.

The field picker includes declared output fields and field paths observed in recent successful executions of that intent within the same tenant. Simulation also adds observed step/output paths to the picker. These paths are suggestions; individual runs may return different optional fields. Missing explicit trigger/step references fail with an error rather than silently substituting a value.

For standalone simulation, supply **Trigger inputs** and **Trigger outputs** JSON. Simulation does not run the vanilla intent. Expand **Inspect inputs and outputs** in the result trace to see each step's actual values. Disconnected nodes are marked and must be linked before they can run.

Administrators can add **Record operation** and **Map outputs** nodes to ordinary flows or custom intents. No predefined intent is required. The entity selector is populated from tenant-scoped application models, including orders, order items, customers, billing, service accounts, products, tickets and training records. Authentication, tenants, execution logs, AI credentials and flow definitions are system-managed and are excluded. System identity and audit columns are read-only.

Choose Read, Insert, Update, Upsert or Delete. The field list shows types, required insert fields, enumerated values and foreign-key targets. **Values** contains fields to write. **Match** contains exact field/value criteria for Read, Update, Upsert and Delete. A match must identify at most one record. Use `id`, `brt_id`, or a natural key; upsert inserts require a natural key, because system IDs are generated automatically.

Use the mapping picker beneath each JSON editor to map a target field from the trigger or an earlier connected step. References preserve numbers, booleans, objects and arrays when the entire value is a reference:

```json
{
  "order_id": "{{steps.order.output.id}}",
  "product_offering_id": "{{steps.product.output.id}}",
  "quantity": "{{entities.quantity}}"
}
```

Every completed step exposes `steps.NODE_ID.input` and `steps.NODE_ID.output`. Nested paths and numeric list indices work, for example `steps.check.output.eligible.0`. Action nodes expose their full returned result, not just IDs. Only executed nodes have outputs, and repeated nodes expose their latest run; the trace retains each visit. Map outputs constructs a named object from constants and references for subsequent steps; it does not execute arbitrary code.

## Example: customer, order and line items

Use nodes with these IDs and connect them sequentially:

1. `customer`: Read `parties`, match `{"brt_id":"{{entities.customer_brt_id}}"}`.
2. `billing`: Upsert `billing_accounts`, match `{"party_id":"{{steps.customer.output.id}}"}`.
3. `service`: Upsert `service_accounts`, match `{"billing_account_id":"{{steps.billing.output.id}}","name":"Home service"}`.
4. `product`: Read `product_offerings`, match `{"id":"{{entities.product_id}}"}`.
5. `order`: Insert `orders`, values `{"party_id":"{{steps.customer.output.id}}","service_account_id":"{{steps.service.output.id}}","monthly_price":"{{entities.monthly_price}}"}`.
6. `line`: Insert `order_items`, using the mapping example above.
7. End.

Add another line-item insert for each additional product. These are record operations: they do not activate a service, charge a customer, send a notification, or run a predefined order intent. Configure any required business steps explicitly. Database defaults and constraints still apply; an order requires a service account even when it has no quote.

All references must belong to the tenant. CRM lifecycle locks remain enforced. Deletes refuse records referenced by other rows; delete those rows explicitly first. Failed record operations stop the flow, and the enclosing transaction rolls back. Simulate shows actual inputs/outputs but rolls back all writes. Only executing the active flow/custom intent persists the changes.

About record now shows **Record number** (the internal numeric ID) and **BRT_ID** together. Match can use either; foreign-key fields such as `order_id` take the numeric ID from a read/insert output.

Deploy the updated backend and frontend together. No database migration is required for these changes. The earlier B2C-only patch script does not deploy this feature.

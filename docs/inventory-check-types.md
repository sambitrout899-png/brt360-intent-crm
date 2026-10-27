# Tenant inventory check types

Apply `alembic upgrade head` in backend before starting the API. The API checks enabled schedules every minute while running; missed schedules run on restart. Minimum interval is 5 minutes. Failed attempts retry at the configured interval, preserving the last successful snapshot.

Set server environment variables per tenant (numeric tenant ID):

- `INVENTORY_CHECK_TYPES_URL_1=https://inventory.example.com/check-types`
- `INVENTORY_CHECK_TYPES_TOKEN_1=...` (optional bearer token)

Each tenant URL/token must return only that tenant's inventory. HTTPS is required; redirects are not followed. The endpoint returns a complete JSON array (maximum 10,000 entries), not a paginated response:

```json
[{"id":"fiber-route", "name":"Fiber Route Availability", "resource_type":"fiber", "is_active":true}]
```

IDs must be stable and unique. Name and resource_type are required. is_active defaults to true. Missing previously imported IDs are deactivated, including when the source returns an empty array. Manual checks and sample checks are preserved. Imported activity follows the source on each refresh.

In Admin → Feasibility Checks, load sample check types without an API connection, or enable periodic refresh and save the schedule. The sample button loads four deterministic checks and can be clicked repeatedly without duplicates. Samples remain active until deactivated or deleted; remove them before production use.

The administrator-only integration intent INT-34 ("Sync inventory check types") performs an immediate real inventory refresh and records the execution in the intent audit trail. The admin sync button and scheduler use the same import service. Credentials are never sent to the browser.

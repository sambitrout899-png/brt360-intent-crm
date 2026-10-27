# B2C direct order (vanilla B2C New Connect)

A B2C customer goes straight from the prospect to an order. There is no lead,
no opportunity, no quote and no Presales check; the **Order Module** runs every
check on the order itself. B2C orders arrive on any channel and one order can
move between channels.

```
B2C prospect (INT-15, segment B2C)  or  consumer (INT-41)
  -> Place a B2C order (INT-104)                      called automatically when products are given
       Order Module checks, on the order:
         service feasibility at the installation address  (the configured feasibility check types)
         product eligibility for the customer
         equipment availability (available-to-promise)
         credit - only when the customer has a credit line
         verification (lines, prices, tax, payment)
       feasible      -> submitted and booked (awaiting shipping when there is equipment)
       not feasible  -> held with the reason (release INT-60 / cancel INT-61)
  -> fulfilment: INT-25 approve & raise installation -> INT-26 -> INT-27 -> INT-28 (billing, assets, account)
```

## What changed

| | Before | Now (vanilla) |
| --- | --- | --- |
| After a B2C prospect | "Generate a lead" (INT-46) | "Place their order" (INT-104); with products on the prospect, the order is placed in the same run |
| Lead / opportunity / quote / Presales for a B2C customer | allowed | refused with a pointer to INT-104 (INT-46, INT-17 approve, INT-05, INT-38) |
| Feasibility for B2C | none (INT-07 activated at once) or on an opportunity | on the order, by the Order Module (`feasibility_assessments.order_id`) |
| Channel of an order | not recorded | recorded on the order, with a reference; every later touch on the order's channel timeline |
| B2B | unchanged | unchanged |

INT-07's consumer path (activate at once, no checks) still works for anyone
who calls it; the vanilla process and the "What's next" steps now use INT-104.

## Channels

IVR, retail sales point, store visit, WhatsApp chat, SMS, USSD, web, mobile app,
call centre, field agent, email. Admin -> Order Management -> **B2C order
journey** chooses which are accepted.

* INT-104 records where the order started (`order_channel`, `channel_reference`
  such as the IVR call id, WhatsApp conversation id or POS terminal).
* INT-105 **Log an order interaction** - "Customer asked about order 7 on
  WhatsApp" - adds the touch and returns the order's status and timeline, so an
  agent on any channel sees the same order.
* Order Management steps (payment, shipping, submit, ...) given an
  `order_channel` also add a touch.
* `GET /api/order-management/orders/{id}` now includes `channel`,
  `installation_address`, `channel_timeline` and the Order Module's
  `feasibility`; `GET /api/order-management/orders/{id}/channels` returns the
  timeline alone.

## Switch

Admin -> Order Management -> B2C order journey (`GET/PUT
/api/admin/order-management/b2c-journey`):

* `direct_order` (default **on** - the vanilla process). Off: B2C prospects may
  take the lead path again.
* `auto_submit` (default on): a feasible order is submitted and booked at once.
* `require_installation_address` (default on).
* `channels`: the accepted order channels.

Consumers without a credit line are not blocked by credit; a consumer order with
nothing paid now is set to "Billed on first invoice".

## Code

`crm_core/b2c_order_service.py` (journey, checks, timeline),
`intent_engine/b2c_intents.py` (INT-104/105 phrases, fields, next steps, training
examples appended last), `orchestration/actions_b2c.py`, migration
`c3e5a7b9d1f4_b2c_direct_order.py`, tests `tests/test_b2c_direct_order.py`.
